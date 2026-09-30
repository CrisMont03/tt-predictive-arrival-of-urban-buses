"""
procesar_video_v4.py — TT 2026-B050

Extiende procesar_video_v3.py con resolución del problema de identidad entre videos
consecutivos mediante matching por posición (centroide del bounding box).

Problema que resuelve:
    Una persona llega a la parada DESPUÉS del último bus de video6AM mientras la cámara
    aún graba. El pipeline de v3 no genera registro para ella porque no hay bus posterior
    en ese video. Cuando se procesa video7AM, esa persona ya está en la parada desde el
    primer frame, pero el pipeline no sabe cuándo llegó realmente.

Solución implementada:
    1. Al terminar de procesar un video, v4 guarda en {nombre}_v4_cola.json todas las
       personas que llegaron DESPUÉS del último bus confirmado, tanto las que siguen en
       la zona al final del video como las que ya salieron (pueden haber salido
       brevemente del ROI o perdido el tracking). Se guarda su arrival_user real y su
       último centroide conocido (posición en el frame).

    2. Al procesar el siguiente video con --cola-anterior, v4 intenta emparejar las
       personas detectadas en los primeros VENTANA_INICIO_SEG segundos con los
       candidatos de la cola usando distancia euclídea entre centroides. Si el match
       es unívoco y está dentro de UMBRAL_MATCH_PX, se usa el arrival_user real del
       candidato en lugar de la hora de detección en el nuevo video.

    3. Si el match es ambiguo (dos o más candidatos dentro del umbral), se descarta
       y la persona se trata como nueva llegada (comportamiento de v3). Los candidatos
       que no encuentran match son ignorados silenciosamente (la persona ya no está).

Por qué guardar_cola incluye personas que ya salieron del ROI:
    En la práctica, las personas que llegaron después del último bus pueden salir
    brevemente del polígono ROI (desplazarse, cambiar de posición) antes de que
    termine el video. Si solo se guardaran las personas aún en zona al final, la cola
    quedaría vacía en la mayoría de los casos. Al incluir también a las que ya salieron,
    el matching del video siguiente actúa como filtro natural: si la persona ya no está
    físicamente, no habrá detección en esa posición y el candidato simplemente no
    encontrará match.

Diferencias respecto a v3:
    - Nuevas constantes: VENTANA_INICIO_SEG, UMBRAL_MATCH_PX
    - Nueva salida por video: {nombre}_v4_cola.json
    - Nuevo parámetro CLI: --cola-anterior
    - Funciones nuevas: centroide_bbox, distancia_euclidea,
                        match_cola_candidato, cargar_cola, guardar_cola
    - En procesar_video: lógica de matching al inicio y guardado de cola al final

Uso:
    # Paso 1 — procesar video6AM (genera la cola automáticamente):
    python procesar_video_v4.py video6AM.mp4 \\
        --timestamp-inicio "2025-06-02 06:00:00"
    # → genera: output/video6AM_v4_cola.json

    # Paso 2 — procesar video7AM con la cola del anterior:
    python procesar_video_v4.py video7AM.mp4 \\
        --timestamp-inicio "2025-06-02 07:00:00" \\
        --cola-anterior output/video6AM_v4_cola.json

Para el dataset de entrenamiento se usa procesar_video_v3.py.
v4 añade registros con arrival_user real para personas carry-over cuando
el match por posición es unívoco; los casos ambiguos se omiten (igual que v3).
"""

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import torch
from tqdm import tqdm
from ultralytics import YOLO

import supervision as sv


# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTES
# ──────────────────────────────────────────────────────────────────────────────

CLASE_PERSONA = 0
CLASE_BUS     = 5

FRAME_INTERVAL = 3

MIN_DWELL_BUS_SEG = 0.2 #0.1
BUS_GAP_TOL_SEG   = 3.0
CONF_BUS_MIN      = 0.30
BUS_RATIO_MIN     = 1.3 #1.1
BUS_AREA_FRAC     = 0.02
BUS_HSV_BAJO      = np.array([15, 25, 140])
BUS_HSV_ALTO      = np.array([32, 180, 255])
BUS_COLOR_FRAC    = 0.08

BUS_HSV_VERDE_BAJO   = np.array([35,  40,  80])
BUS_HSV_VERDE_ALTO   = np.array([85, 255, 255])
BUS_COLOR_VERDE_FRAC = 0.0 # 0.008

BUS_BLANCO_S_MAX    = 30
BUS_BLANCO_V_MIN    = 150
BUS_BLANCO_FRAC_MAX = 0.15 #0.2

BUS_HSV_PURPURA_BAJO  = np.array([100,  50,  50])
BUS_HSV_PURPURA_ALTO  = np.array([160, 255, 255])
BUS_PURPURA_FRAC_MAX  = 0.05
COOLDOWN_BUS_SEG  = 130.0

MIN_PRESENCIA_PERSONA_SEG = 1.2
COOLDOWN_REENTRADA_SEG    = 20.0

ID_SWITCH_VENTANA_SEG = 25.0
ID_SWITCH_DIST_MAX    = 80

RESET_ESPERA_SEG = 15.0
MIN_ESPERA_SEG   = 5.0
MAX_ESPERA_SEG   = 45 * 60

# ── Parámetros de matching por posición entre videos ──────────────────────────
VENTANA_INICIO_SEG = 60.0   # segundos desde el inicio del video donde se intenta
                             # emparejar personas con candidatos de la cola anterior
UMBRAL_MATCH_PX    = 80     # distancia máxima (px) entre centroides para considerar
                             # que dos detecciones corresponden a la misma persona


# ──────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class EstadoPersona:
    track_id:       int
    tiempo_entrada: datetime


# ──────────────────────────────────────────────────────────────────────────────
# UTILIDADES GENERALES
# ──────────────────────────────────────────────────────────────────────────────

def cargar_zonas(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_timestamp(texto: str) -> datetime:
    return datetime.strptime(texto.strip(), "%Y-%m-%d %H:%M:%S")


def frame_a_tiempo(frame_id: int, fps: float, inicio: datetime) -> datetime:
    return inicio + timedelta(seconds=frame_id / fps)


def seleccionar_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def calcular_metricas_bus(frame: np.ndarray, bbox, w_vid: int, h_vid: int) -> dict:
    x1, y1, x2, y2 = map(int, bbox)
    x1 = max(x1, 0); y1 = max(y1, 0)
    x2 = min(x2, frame.shape[1]); y2 = min(y2, frame.shape[0])
    ancho = x2 - x1
    alto  = y2 - y1
    area  = ancho * alto

    metricas = {
        "bbox":            (x1, y1, x2, y2),
        "ancho_px":        ancho,
        "alto_px":         alto,
        "ratio":           round(ancho / (alto + 1e-6), 3),
        "area_px":         area,
        "area_frac":       round(area / (w_vid * h_vid), 4),
        "frac_blanco":     None,
        "frac_purpura":    None,
        "frac_amarillo":   None,
        "frac_verde":      None,
        "pasa_ratio":      ancho / (alto + 1e-6) >= BUS_RATIO_MIN,
        "pasa_area":       area >= w_vid * h_vid * BUS_AREA_FRAC,
        "pasa_blanco":     None,
        "pasa_purpura":    None,
        "pasa_color":      None,
    }

    recorte = frame[y1 : y1 + alto // 2, x1:x2]
    if recorte.size > 0:
        hsv   = cv2.cvtColor(recorte, cv2.COLOR_BGR2HSV)
        total = recorte.shape[0] * recorte.shape[1]

        frac_blanco   = np.sum((hsv[:, :, 1] < BUS_BLANCO_S_MAX) & (hsv[:, :, 2] > BUS_BLANCO_V_MIN)) / total
        frac_purpura  = np.sum(cv2.inRange(hsv, BUS_HSV_PURPURA_BAJO, BUS_HSV_PURPURA_ALTO) > 0) / total
        frac_amarillo = np.sum(cv2.inRange(hsv, BUS_HSV_BAJO,         BUS_HSV_ALTO)         > 0) / total
        frac_verde    = np.sum(cv2.inRange(hsv, BUS_HSV_VERDE_BAJO,   BUS_HSV_VERDE_ALTO)   > 0) / total

        metricas["frac_blanco"]   = round(frac_blanco,   4)
        metricas["frac_purpura"]  = round(frac_purpura,  4)
        metricas["frac_amarillo"] = round(frac_amarillo, 4)
        metricas["frac_verde"]    = round(frac_verde,    4)
        metricas["pasa_blanco"]   = frac_blanco   <  BUS_BLANCO_FRAC_MAX
        metricas["pasa_purpura"]  = frac_purpura  <  BUS_PURPURA_FRAC_MAX
        metricas["pasa_color"]    = frac_amarillo >= BUS_COLOR_FRAC and frac_verde > BUS_COLOR_VERDE_FRAC

    return metricas


def es_bus_objetivo(frame: np.ndarray, bbox) -> bool:
    x1, y1, x2, y2 = map(int, bbox)
    x1 = max(x1, 0); y1 = max(y1, 0)
    x2 = min(x2, frame.shape[1]); y2 = min(y2, frame.shape[0])
    alto = y2 - y1
    if alto < 10 or (x2 - x1) < 10:
        return False
    recorte = frame[y1 : y1 + alto // 2, x1:x2]
    if recorte.size == 0:
        return False
    hsv   = cv2.cvtColor(recorte, cv2.COLOR_BGR2HSV)
    total = recorte.shape[0] * recorte.shape[1]

    mascara_blanco = (hsv[:, :, 1] < BUS_BLANCO_S_MAX) & (hsv[:, :, 2] > BUS_BLANCO_V_MIN)
    if np.sum(mascara_blanco) / total >= BUS_BLANCO_FRAC_MAX:
        return False

    frac_purpura = np.sum(cv2.inRange(hsv, BUS_HSV_PURPURA_BAJO, BUS_HSV_PURPURA_ALTO) > 0) / total
    if frac_purpura >= BUS_PURPURA_FRAC_MAX:
        return False

    frac_amarillo = np.sum(cv2.inRange(hsv, BUS_HSV_BAJO,       BUS_HSV_ALTO)       > 0) / total
    frac_verde    = np.sum(cv2.inRange(hsv, BUS_HSV_VERDE_BAJO, BUS_HSV_VERDE_ALTO) > 0) / total

    return frac_amarillo >= BUS_COLOR_FRAC and frac_verde > BUS_COLOR_VERDE_FRAC


def extraer_ids_sin_tracker(det: sv.Detections, zona: sv.PolygonZone) -> set:
    if len(det) == 0:
        return set()
    mascara = zona.trigger(detections=det)
    if not np.any(mascara):
        return set()
    return set(int(i) for i in np.where(mascara)[0])


def extraer_ids_en_zona(det: sv.Detections, zona: sv.PolygonZone) -> set:
    if len(det) == 0 or det.tracker_id is None:
        return set()
    mascara = zona.trigger(detections=det)
    if not np.any(mascara):
        return set()
    return set(int(tid) for tid in det.tracker_id[mascara])


def puede_confirmar_bus(t: datetime, llegadas: list) -> bool:
    if not llegadas:
        return True
    return (t - max(llegadas)).total_seconds() >= COOLDOWN_BUS_SEG


# ──────────────────────────────────────────────────────────────────────────────
# RECUPERACIÓN DE ID SWITCH
# ──────────────────────────────────────────────────────────────────────────────

def resolver_tid(
    tid_nuevo:       int,
    bbox:            np.ndarray,
    t_actual:        datetime,
    tracks_perdidos: dict,
    tid_canonico:    dict,
) -> int:
    if tid_nuevo in tid_canonico:
        return tid_canonico[tid_nuevo]

    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2

    mejor_tid  = None
    mejor_dist = float("inf")

    for tid_orig, (t_lost, bbox_old) in tracks_perdidos.items():
        if (t_actual - t_lost).total_seconds() > ID_SWITCH_VENTANA_SEG:
            continue
        cx_o = (bbox_old[0] + bbox_old[2]) / 2
        cy_o = (bbox_old[1] + bbox_old[3]) / 2
        dist = ((cx - cx_o) ** 2 + (cy - cy_o) ** 2) ** 0.5
        if dist < mejor_dist:
            mejor_dist = dist
            mejor_tid  = tid_orig

    if mejor_tid is not None and mejor_dist <= ID_SWITCH_DIST_MAX:
        tid_canonico[tid_nuevo] = mejor_tid
        return mejor_tid

    return tid_nuevo


# ──────────────────────────────────────────────────────────────────────────────
# MATCHING POR POSICIÓN ENTRE VIDEOS (cola anterior)
# ──────────────────────────────────────────────────────────────────────────────

def centroide_bbox(bbox: np.ndarray) -> tuple[float, float]:
    """Devuelve el centroide (cx, cy) de un bounding box [x1, y1, x2, y2]."""
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def distancia_euclidea(cx1: float, cy1: float, cx2: float, cy2: float) -> float:
    return ((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2) ** 0.5


def match_cola_candidato(
    cx:         float,
    cy:         float,
    candidatos: list[dict],
    usados:     set[int],
) -> tuple[int | None, float]:
    """
    Busca el candidato de cola más cercano al centroide (cx, cy).

    Retorna (índice, distancia) si el match es unívoco dentro del umbral.
    Retorna (None, inf) si no hay candidato en rango o si el match es ambiguo.

    Ambigüedad: dos o más candidatos dentro de UMBRAL_MATCH_PX cuya diferencia
    de distancia entre el primero y el segundo es menor a UMBRAL_MATCH_PX * 0.5.
    En ese caso, no se asigna ninguno para evitar errores de identidad.
    """
    dentro = []
    for i, c in enumerate(candidatos):
        if i in usados:
            continue
        ccx, ccy = c["last_centroid"]
        d = distancia_euclidea(cx, cy, ccx, ccy)
        if d <= UMBRAL_MATCH_PX:
            dentro.append((d, i))

    if not dentro:
        return None, float("inf")

    dentro.sort()

    if len(dentro) == 1:
        return dentro[0][1], dentro[0][0]

    # Dos o más candidatos: el match es válido solo si el mejor está claramente
    # más cerca que el segundo (diferencia > mitad del umbral).
    d_mejor   = dentro[0][0]
    d_segundo = dentro[1][0]
    if (d_segundo - d_mejor) > UMBRAL_MATCH_PX * 0.5:
        return dentro[0][1], d_mejor

    return None, float("inf")


def cargar_cola(path: str) -> list[dict]:
    """
    Carga el archivo _v4_cola.json generado por el video anterior.
    Devuelve lista de candidatos con 'arrival_user' y 'last_centroid'.
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def guardar_cola(
    personas_en_zona:   dict,
    periodos_persona:   list,
    tracks_ultimo_bbox: dict,
    llegadas_bus:       list,
    output_path:        Path,
) -> int:
    """
    Guarda en JSON los candidatos carry-over para el siguiente video.

    Incluye DOS grupos de personas, ambas con arrival_user posterior al último bus:

    Grupo A — aún en zona al final del video (personas_en_zona):
        Son las candidatas más probables. Están físicamente presentes cuando
        termina la grabación.

    Grupo B — ya salieron de la zona antes de que terminara el video (periodos_persona):
        Pueden haber salido brevemente del polígono ROI, haberse desplazado, o
        el tracker haberlas perdido momentáneamente. El matching del video siguiente
        actúa como filtro: si ya no están físicamente, no habrá match.

    Por qué incluir el Grupo B:
        Si solo se guarda el Grupo A, la cola queda vacía cuando todas las personas
        detectadas después del último bus salen del ROI antes de que termine el video
        (el caso más común). Al incluir el Grupo B, el matching en el siguiente video
        recupera los datos reales cuando la persona sigue presente, y los descarta
        silenciosamente cuando ya se fue.

    Retorna el número total de candidatos guardados.
    """
    t_ultimo_bus = max(llegadas_bus) if llegadas_bus else None
    cola   = []
    vistos: set[int] = set()

    # ── Grupo A: personas aún en zona al final del video ─────────────────────
    for tid, estado in personas_en_zona.items():
        if t_ultimo_bus is not None and estado.tiempo_entrada <= t_ultimo_bus:
            continue
        bbox = tracks_ultimo_bbox.get(tid)
        if bbox is None:
            continue
        cx, cy = centroide_bbox(bbox)
        cola.append({
            "arrival_user":  estado.tiempo_entrada.strftime("%Y-%m-%d %H:%M:%S"),
            "last_centroid": [round(float(cx), 1), round(float(cy), 1)],
        })
        vistos.add(tid)

    # ── Grupo B: personas que ya salieron pero llegaron después del último bus ─
    for p in periodos_persona:
        tid = p["track_id"]
        if tid in vistos:
            continue                                   # ya capturado en Grupo A
        t_entrada = p["entrada"]
        if t_ultimo_bus is not None and t_entrada <= t_ultimo_bus:
            continue                                   # llegó antes del último bus
        bbox = tracks_ultimo_bbox.get(tid)
        if bbox is None:
            continue
        cx, cy = centroide_bbox(bbox)
        cola.append({
            "arrival_user":  t_entrada.strftime("%Y-%m-%d %H:%M:%S"),
            "last_centroid": [round(float(cx), 1), round(float(cy), 1)],
        })
        vistos.add(tid)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cola, f, indent=2, ensure_ascii=False)

    return len(cola)


# ──────────────────────────────────────────────────────────────────────────────
# POST-PROCESAMIENTO
# ──────────────────────────────────────────────────────────────────────────────

def calcular_tiempos_espera(
    llegadas_bus:    list,
    periodos_persona: list,
) -> list:
    """
    Para cada bus confirmado:
      - Si había personas esperando → genera una fila por persona.
      - Si NO había personas esperando → genera fila solo-bus (track_id=-1).

    Las personas carry-over (arrival_user anterior al inicio del video actual)
    se procesan igual: t_efectivo = max(t_entrada, t_inicio_intervalo).
    Si t_entrada viene del video anterior, el waiting_time calculado es real.
    """
    if not llegadas_bus:
        return []

    llegadas_sorted = sorted(llegadas_bus)
    registros = []

    for idx, t_bus in enumerate(llegadas_sorted):
        t_inicio = (
            datetime.min
            if idx == 0
            else llegadas_sorted[idx - 1] + timedelta(seconds=RESET_ESPERA_SEG)
        )

        registros_este_bus = []

        for p in periodos_persona:
            t_entrada = p["entrada"]
            t_salida  = p["salida"]

            if t_entrada >= t_bus:
                continue
            if t_salida is not None and t_salida < t_bus:  # if t_salida is not None and t_salida <= t_inicio:
                continue

            t_efectivo = max(t_entrada, t_inicio)
            if t_efectivo >= t_bus:
                continue

            espera_seg = (t_bus - t_efectivo).total_seconds()
            if espera_seg <= 0:
                continue
            if not (MIN_ESPERA_SEG <= espera_seg <= MAX_ESPERA_SEG):
                continue

            registros_este_bus.append({
                "track_id_persona": p["track_id"],
                "arrival_user":     t_efectivo.strftime("%Y-%m-%d %H:%M:%S"),
                "bus_arrival":      t_bus.strftime("%Y-%m-%d %H:%M:%S"),
                "waiting_time_min": round(espera_seg / 60, 3),
                "waiting_time_seg": round(espera_seg, 1),
                "date":             t_efectivo.strftime("%Y-%m-%d"),
                "hour":             t_efectivo.hour,
                "day":              t_efectivo.strftime("%A"),
                "is_weekend":       t_efectivo.weekday() >= 5,
            })

        if registros_este_bus:
            registros.extend(registros_este_bus)
        else:
            registros.append({
                "track_id_persona": -1,
                "arrival_user":     t_bus.strftime("%Y-%m-%d %H:%M:%S"),
                "bus_arrival":      t_bus.strftime("%Y-%m-%d %H:%M:%S"),
                "waiting_time_min": 0.0,
                "waiting_time_seg": 0.0,
                "date":             t_bus.strftime("%Y-%m-%d"),
                "hour":             t_bus.hour,
                "day":              t_bus.strftime("%A"),
                "is_weekend":       t_bus.weekday() >= 5,
            })

    return registros


# ──────────────────────────────────────────────────────────────────────────────
# VISUALIZACIÓN
# ──────────────────────────────────────────────────────────────────────────────

def dibujar_frame(
    frame:          np.ndarray,
    det_buses:      sv.Detections,
    det_personas:   sv.Detections,
    roi_bus_pts:    np.ndarray,
    roi_person_pts: np.ndarray,
    t_actual:       datetime,
    n_buses:        int,
    n_espera:       int,
) -> np.ndarray:
    out = frame.copy()

    cv2.polylines(out, [roi_bus_pts],    True, (0,  60, 220), 2)
    cv2.polylines(out, [roi_person_pts], True, (220, 60,  0), 2)

    if len(det_buses) > 0:
        for i, bbox in enumerate(det_buses.xyxy):
            x1, y1, x2, y2 = map(int, bbox)
            cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 0), 2)
            conf = det_buses.confidence[i] if det_buses.confidence is not None else 0
            cv2.putText(out, f"BUS {conf:.2f}", (x1, max(y1 - 6, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 0), 2, cv2.LINE_AA)

    if len(det_personas) > 0 and det_personas.tracker_id is not None:
        for bbox, tid in zip(det_personas.xyxy, det_personas.tracker_id):
            x1, y1, x2, y2 = map(int, bbox)
            cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 200), 2)
            cv2.putText(out, f"P{tid}", (x1, max(y1 - 4, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 200), 1, cv2.LINE_AA)

    cv2.rectangle(out, (5, 5), (355, 105), (0, 0, 0), -1)
    cv2.putText(out, t_actual.strftime("%Y-%m-%d  %H:%M:%S"),
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (0, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(out, f"Buses confirmados : {n_buses}",
                (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 200, 0),   1, cv2.LINE_AA)
    cv2.putText(out, f"Personas en espera: {n_espera}",
                (10, 84), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 200, 200), 1, cv2.LINE_AA)

    return out


# ──────────────────────────────────────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────────────────────────────────────

def procesar_video(
    video_path:         str,
    zonas_path:         str,
    timestamp_inicio:   str,
    modelo_nombre:      str       = "yolov8m.pt",
    guardar_video:      bool      = False,
    output_dir:         str       = "output",
    cola_anterior_path: str | None = None,
) -> None:

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    nombre_base = Path(video_path).stem

    print(f"\n{'='*60}")
    print(f"  Video : {video_path}")
    print(f"  Inicio: {timestamp_inicio}")
    print(f"  Modo  : v4 (matching por posición para carry-over entre videos)")
    if cola_anterior_path:
        print(f"  Cola  : {cola_anterior_path}")
    print(f"{'='*60}\n")

    zonas    = cargar_zonas(zonas_path)
    t_inicio = parse_timestamp(timestamp_inicio)
    device   = seleccionar_device()

    roi_bus_pts    = np.array(zonas["ROI_BUS"],    dtype=np.int32)
    roi_person_pts = np.array(zonas["ROI_PERSON"], dtype=np.int32)

    zona_bus    = sv.PolygonZone(polygon=roi_bus_pts)
    zona_person = sv.PolygonZone(
        polygon=roi_person_pts,
        triggering_anchors=[sv.Position.CENTER],
    )

    # ── Cargar candidatos de la cola anterior ─────────────────────────────────
    cola_candidatos: list[dict]        = []
    cola_usados:     set[int]          = set()
    tid_a_arrival_real: dict[int, datetime] = {}

    if cola_anterior_path:
        cola_candidatos = cargar_cola(cola_anterior_path)
        print(f"[Cola] {len(cola_candidatos)} candidato(s) carry-over cargado(s) "
              f"desde {cola_anterior_path}")
        for i, c in enumerate(cola_candidatos):
            print(f"       [{i}] arrival_user={c['arrival_user']}  "
                  f"centroide={c['last_centroid']}")
        print()

    print(f"[1/5] Cargando {modelo_nombre}  (device={device}) ...")
    modelo = YOLO(modelo_nombre)
    print(f"      Listo.\n")

    cap_meta     = cv2.VideoCapture(video_path)
    fps_real     = cap_meta.get(cv2.CAP_PROP_FPS) or 15.0
    total_frames = int(cap_meta.get(cv2.CAP_PROP_FRAME_COUNT))
    w_vid        = int(cap_meta.get(cv2.CAP_PROP_FRAME_WIDTH))
    h_vid        = int(cap_meta.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap_meta.release()

    fps_efectivo = fps_real / FRAME_INTERVAL
    frames_proc  = total_frames // FRAME_INTERVAL

    print(f"[2/5] {fps_real:.1f} fps  |  {total_frames} frames totales  |  "
            f"procesando 1 de cada {FRAME_INTERVAL} → {fps_efectivo:.1f} fps efectivos\n")

    buf_pers = max(int(fps_efectivo * 10), 15)

    tracker_persona = sv.ByteTrack(
        track_activation_threshold = 0.30,
        lost_track_buffer          = buf_pers,
        minimum_matching_threshold = 0.60,
        frame_rate                 = int(fps_efectivo),
        minimum_consecutive_frames = 3,
    )

    # ── Estado: buses ─────────────────────────────────────────────────────────
    bus_zona_activa:     bool            = False
    bus_zona_entrada:    datetime | None = None
    bus_zona_confirmado: bool            = False
    bus_zona_ultimo_det: datetime | None = None
    bus_zona_dwell_acum: float           = 0.0
    bus_zona_metricas:   dict            = {}
    llegadas_bus_confirmadas: list       = []
    metricas_txt_lines:  list            = []

    frame_dt = FRAME_INTERVAL / fps_real

    # ── Estado: personas ──────────────────────────────────────────────────────
    personas_pendientes: dict = {}
    personas_en_zona:    dict = {}
    periodos_persona:    list = []
    ultimas_salidas:     dict = {}

    # ── Estado: track-lifetime deduplication ──────────────────────────────────
    tracks_ultimo_bbox: dict = {}
    tracks_perdidos:    dict = {}
    tid_canonico:       dict = {}

    bus_freeze_hasta: datetime | None = None
    ids_persona_prev: set = set()

    # ── Video writer ──────────────────────────────────────────────────────────
    writer       = None
    ruta_vid_out = None
    if guardar_video:
        ruta_vid_out = str(Path(output_dir) / f"{nombre_base}_anotado_v4.mp4")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(ruta_vid_out, fourcc, fps_efectivo, (w_vid, h_vid))

    # ── Loop principal ────────────────────────────────────────────────────────
    print("[3/5] Procesando frames...\n")
    cap      = cv2.VideoCapture(video_path)
    frame_id = 0

    with tqdm(total=frames_proc, unit="frame", ncols=72) as pbar:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            if frame_id % FRAME_INTERVAL != 0:
                frame_id += 1
                continue

            t_actual         = frame_a_tiempo(frame_id, fps_real, t_inicio)
            seg_desde_inicio = (t_actual - t_inicio).total_seconds()

            # ── Detección ────────────────────────────────────────────────────
            resultado = modelo(
                frame,
                device  = device,
                classes = [CLASE_PERSONA, CLASE_BUS],
                conf    = 0.25,
                imgsz   = 1280,
                verbose = False,
            )[0]

            det_all = sv.Detections.from_ultralytics(resultado)

            bus_mask = det_all.class_id == CLASE_BUS
            if np.any(bus_mask) and det_all.confidence is not None:
                bus_mask = bus_mask & (det_all.confidence >= CONF_BUS_MIN)
            det_buses = det_all[bus_mask]

            if len(det_buses) > 0:
                bboxes   = det_buses.xyxy
                w        = bboxes[:, 2] - bboxes[:, 0]
                h        = bboxes[:, 3] - bboxes[:, 1]
                ratio    = w / (h + 1e-6)
                area     = w * h
                area_min = w_vid * h_vid * BUS_AREA_FRAC
                det_buses = det_buses[(ratio >= BUS_RATIO_MIN) & (area >= area_min)]

            if len(det_buses) > 0:
                color_mask = np.array([es_bus_objetivo(frame, b) for b in det_buses.xyxy])
                det_buses  = det_buses[color_mask]

            det_personas = det_all[det_all.class_id == CLASE_PERSONA]

            # ── Tracking ByteTrack (personas) ─────────────────────────────────
            det_personas = tracker_persona.update_with_detections(det_personas)

            if det_personas.tracker_id is not None:
                for bbox_raw, tid_raw in zip(det_personas.xyxy, det_personas.tracker_id):
                    tracks_ultimo_bbox[int(tid_raw)] = bbox_raw.copy()

            for tid_exp in list(tracks_perdidos):
                t_lost, _ = tracks_perdidos[tid_exp]
                if (t_actual - t_lost).total_seconds() > ID_SWITCH_VENTANA_SEG:
                    del tracks_perdidos[tid_exp]

            hay_bus_zona      = len(extraer_ids_sin_tracker(det_buses, zona_bus)) > 0
            ids_raw_zona      = extraer_ids_en_zona(det_personas, zona_person)
            ids_persona_actual: set = set()

            for tid_raw in ids_raw_zona:
                bbox_raw = tracks_ultimo_bbox.get(tid_raw)
                if bbox_raw is None:
                    ids_persona_actual.add(tid_raw)
                    continue
                tid_can = resolver_tid(
                    tid_raw, bbox_raw, t_actual, tracks_perdidos, tid_canonico
                )
                if tid_can != tid_raw:
                    tracks_ultimo_bbox[tid_can] = bbox_raw.copy()
                ids_persona_actual.add(tid_can)

            # ════════════════════════════════════════════════════════════════
            # LÓGICA DE BUSES
            # ════════════════════════════════════════════════════════════════
            if hay_bus_zona:
                bus_zona_ultimo_det = t_actual
                if not bus_zona_activa:
                    bus_zona_activa     = True
                    bus_zona_entrada    = t_actual
                    bus_zona_confirmado = False
                    bus_zona_dwell_acum = 0.0
                    if len(det_buses) > 0:
                        bus_zona_metricas = calcular_metricas_bus(
                            frame, det_buses.xyxy[0], w_vid, h_vid
                        )
                elif not bus_zona_confirmado:
                    bus_zona_dwell_acum += frame_dt
                    if bus_zona_dwell_acum >= MIN_DWELL_BUS_SEG:
                        if puede_confirmar_bus(t_actual, llegadas_bus_confirmadas):
                            bus_zona_confirmado = True
                            llegadas_bus_confirmadas.append(t_actual)
                            m = bus_zona_metricas
                            bloque = (
                                f"\n  ╔══ Bus REGISTRADO ══════════════════════════════╗\n"
                                f"  ║  Timestamp       : {t_actual.strftime('%H:%M:%S')}\n"
                                f"  ║  Bus #{len(llegadas_bus_confirmadas)}\n"
                                f"  ╠══ Geometría ════════════════════════════════════╣\n"
                                f"  ║  Bbox            : {m.get('bbox')}\n"
                                f"  ║  Ancho x Alto    : {m.get('ancho_px')} x {m.get('alto_px')} px\n"
                                f"  ║  Ratio (a/h)     : {m.get('ratio')}  (mín: {BUS_RATIO_MIN})  {'✓' if m.get('pasa_ratio') else '✗'}\n"
                                f"  ║  Área fracción   : {m.get('area_frac')}  (mín: {BUS_AREA_FRAC})  {'✓' if m.get('pasa_area') else '✗'}\n"
                                f"  ╠══ Color (recorte superior del bbox) ════════════╣\n"
                                f"  ║  Frac. blanco    : {m.get('frac_blanco')}  (máx: {BUS_BLANCO_FRAC_MAX})  {'✓' if m.get('pasa_blanco') else '✗'}\n"
                                f"  ║  Frac. púrpura   : {m.get('frac_purpura')}  (máx: {BUS_PURPURA_FRAC_MAX})  {'✓' if m.get('pasa_purpura') else '✗'}\n"
                                f"  ║  Frac. amarillo  : {m.get('frac_amarillo')}  (mín: {BUS_COLOR_FRAC})  {'✓' if m.get('frac_amarillo', 0) >= BUS_COLOR_FRAC else '–'}\n"
                                f"  ║  Frac. verde     : {m.get('frac_verde')}  (mín: {BUS_COLOR_VERDE_FRAC})  {'✓' if m.get('frac_verde', 0) >= BUS_COLOR_VERDE_FRAC else '–'}\n"
                                f"  ║  Pasa color (OR) : {'✓' if m.get('pasa_color') else '✗'}\n"
                                f"  ╠══ Confirmación ═════════════════════════════════╣\n"
                                f"  ║  Dwell acum.     : {round(bus_zona_dwell_acum, 2)}s  (mín: {MIN_DWELL_BUS_SEG}s)\n"
                                f"  ║  Cooldown OK     : ✓  (último bus hace >{COOLDOWN_BUS_SEG}s)\n"
                                f"  ╚════════════════════════════════════════════════╝\n"
                            )
                            tqdm.write(bloque)
                            metricas_txt_lines.append(bloque)
            else:
                if bus_zona_activa and bus_zona_ultimo_det is not None:
                    gap = (t_actual - bus_zona_ultimo_det).total_seconds()
                    if gap > BUS_GAP_TOL_SEG:
                        if bus_zona_confirmado:
                            bus_freeze_hasta = t_actual + timedelta(seconds=RESET_ESPERA_SEG)
                            personas_pendientes.clear()
                        bus_zona_activa     = False
                        bus_zona_entrada    = None
                        bus_zona_confirmado = False
                        bus_zona_dwell_acum = 0.0

            # ════════════════════════════════════════════════════════════════
            # LÓGICA DE PERSONAS
            # ════════════════════════════════════════════════════════════════

            for tid in ids_persona_actual - ids_persona_prev:
                if tid in personas_pendientes or tid in personas_en_zona:
                    continue
                if bus_freeze_hasta is not None and t_actual < bus_freeze_hasta:
                    continue
                ultima = ultimas_salidas.get(tid)
                if ultima and (t_actual - ultima).total_seconds() < COOLDOWN_REENTRADA_SEG:
                    continue

                # ── Matching carry-over: ventana de inicio del video ──────────
                # Solo se intenta si hay candidatos sin usar y estamos dentro de
                # los primeros VENTANA_INICIO_SEG segundos del video.
                if cola_candidatos and seg_desde_inicio <= VENTANA_INICIO_SEG:
                    bbox_actual = tracks_ultimo_bbox.get(tid)
                    if bbox_actual is not None:
                        cx, cy = centroide_bbox(bbox_actual)
                        idx_match, dist_match = match_cola_candidato(
                            cx, cy, cola_candidatos, cola_usados
                        )
                        if idx_match is not None:
                            cola_usados.add(idx_match)
                            t_real = parse_timestamp(
                                cola_candidatos[idx_match]["arrival_user"]
                            )
                            tid_a_arrival_real[tid] = t_real
                            tqdm.write(
                                f"  Cola match | track={tid:3d} | "
                                f"arrival_real={t_real.strftime('%H:%M:%S')} "
                                f"(dist={dist_match:.1f}px)"
                            )

                personas_pendientes[tid] = t_actual

            for tid in ids_persona_actual:
                if tid not in personas_pendientes:
                    continue
                dwell = (t_actual - personas_pendientes[tid]).total_seconds()
                if dwell < MIN_PRESENCIA_PERSONA_SEG:
                    continue
                t_arr = personas_pendientes.pop(tid)
                # Si este track tiene un arrival_user real del video anterior,
                # se usa ese en lugar del timestamp de detección actual.
                t_arr = tid_a_arrival_real.pop(tid, t_arr)
                personas_en_zona[tid] = EstadoPersona(tid, t_arr)
                tqdm.write(
                    f"  Persona    | track={tid:3d} | "
                    f"llego {t_arr.strftime('%H:%M:%S')}"
                )

            for tid in ids_persona_prev - ids_persona_actual:
                personas_pendientes.pop(tid, None)
                tid_a_arrival_real.pop(tid, None)   # limpiar si salió antes de confirmar
                if tid in personas_en_zona:
                    estado = personas_en_zona.pop(tid)
                    ultimas_salidas[tid] = t_actual
                    periodos_persona.append({
                        "track_id": estado.track_id,
                        "entrada":  estado.tiempo_entrada,
                        "salida":   t_actual,
                    })
                bbox_last = tracks_ultimo_bbox.get(tid)
                if bbox_last is not None:
                    tracks_perdidos[tid] = (t_actual, bbox_last.copy())

            ids_persona_prev = ids_persona_actual.copy()

            if writer is not None:
                frame_out = dibujar_frame(
                    frame, det_buses, det_personas,
                    roi_bus_pts, roi_person_pts,
                    t_actual,
                    len(llegadas_bus_confirmadas),
                    len(personas_en_zona),
                )
                writer.write(frame_out)

            frame_id += 1
            pbar.update(1)

    cap.release()
    if writer:
        writer.release()

    # Personas aún en zona al terminar el video
    for estado in personas_en_zona.values():
        periodos_persona.append({
            "track_id": estado.track_id,
            "entrada":  estado.tiempo_entrada,
            "salida":   None,
        })

    # ── Guardar cola para el siguiente video ──────────────────────────────────
    # Se llama DESPUÉS de añadir las personas restantes a periodos_persona para
    # que guardar_cola tenga acceso completo a ambos grupos.
    cola_path = Path(output_dir) / f"{nombre_base}_v4_cola.json"
    n_cola = guardar_cola(
        personas_en_zona,
        periodos_persona,
        tracks_ultimo_bbox,
        llegadas_bus_confirmadas,
        cola_path,
    )
    if n_cola > 0:
        print(f"\n[Cola] {n_cola} candidato(s) carry-over guardado(s) → {cola_path}")
    else:
        print(f"\n[Cola] Sin candidatos carry-over al final del video → {cola_path} (vacío)")

    # ── Post-procesamiento ────────────────────────────────────────────────────
    print(f"\n[4/5] Post-proceso ...")
    print(f"      Buses confirmados  : {len(llegadas_bus_confirmadas)}")
    print(f"      Periodos de persona: {len(periodos_persona)}")

    registros = calcular_tiempos_espera(llegadas_bus_confirmadas, periodos_persona)

    vistos: set = set()
    registros_unicos = []
    for r in sorted(registros, key=lambda x: x["arrival_user"]):
        if r["track_id_persona"] == -1:
            registros_unicos.append(r)
            continue
        tid = r["track_id_persona"]
        if tid not in vistos:
            vistos.add(tid)
            registros_unicos.append(r)
    registros = registros_unicos

    n_con_usuarios = sum(1 for r in registros if r["track_id_persona"] != -1)
    n_solo_bus     = sum(1 for r in registros if r["track_id_persona"] == -1)
    n_carry_over   = sum(
        1 for r in registros
        if r["track_id_persona"] != -1
        and r["arrival_user"] < timestamp_inicio
    )

    print(f"      Registros con usuario  : {n_con_usuarios}")
    print(f"        └─ carry-over (v4)   : {n_carry_over}")
    print(f"      Registros solo-bus     : {n_solo_bus}")
    print(f"      Total registros        : {len(registros)}")

    # ── Exportar ──────────────────────────────────────────────────────────────
    print(f"\n[5/5] Exportando ...")

    csv_path = Path(output_dir) / f"{nombre_base}_v4_dataset.csv"
    CSV_COLS = [
        "track_id_persona", "arrival_user", "bus_arrival",
        "waiting_time_min", "waiting_time_seg",
        "date", "hour", "day", "is_weekend",
    ]
    if registros:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer_csv = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
            writer_csv.writeheader()
            writer_csv.writerows(registros)
        print(f"      Dataset  : {csv_path}")
    else:
        print("      Sin registros en el dataset.")
        print("      Revisa: timestamp correcto, zonas bien definidas, conf suficiente.")

    eventos_path = Path(output_dir) / f"{nombre_base}_v4_eventos.json"
    eventos = {
        "video":            video_path,
        "timestamp_inicio": timestamp_inicio,
        "cola_anterior":    cola_anterior_path,
        "llegadas_bus": [
            t.strftime("%Y-%m-%d %H:%M:%S") for t in llegadas_bus_confirmadas
        ],
        "llegadas_usuario": [
            {
                "track_id_persona": r["track_id_persona"],
                "arrival_user":     r["arrival_user"],
                "carry_over":       r["arrival_user"] < timestamp_inicio,
            }
            for r in registros if r["track_id_persona"] != -1
        ],
    }
    with open(eventos_path, "w", encoding="utf-8") as f:
        json.dump(eventos, f, indent=2, ensure_ascii=False)
    print(f"      Eventos  : {eventos_path}")

    if metricas_txt_lines:
        metricas_txt_path = Path(output_dir) / f"{nombre_base}_v4_metricas_bus.txt"
        with open(metricas_txt_path, "w", encoding="utf-8") as f:
            f.writelines(metricas_txt_lines)
        print(f"      Métricas : {metricas_txt_path}")

    print(f"      Cola     : {cola_path}")

    if ruta_vid_out:
        print(f"      Video    : {ruta_vid_out}")

    if registros:
        tiempos = [r["waiting_time_min"] for r in registros if r["track_id_persona"] != -1]
        print(f"\n{'='*60}")
        print(f"  Total registros         : {len(registros)}")
        print(f"  ├─ Con usuarios         : {n_con_usuarios}")
        print(f"  │    └─ carry-over (v4) : {n_carry_over}")
        print(f"  └─ Solo-bus             : {n_solo_bus}")
        if tiempos:
            print(f"  Espera promedio         : {sum(tiempos) / len(tiempos):.2f} min")
            print(f"  Espera minima           : {min(tiempos):.2f} min")
            print(f"  Espera maxima           : {max(tiempos):.2f} min")
        print(f"{'='*60}\n")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "TT 2026-B050 | Pipeline v4 — matching por posición para personas "
            "carry-over entre videos consecutivos"
        )
    )
    p.add_argument("video",
        help="Ruta al video de vigilancia")
    p.add_argument("--timestamp-inicio", required=True,
        help="Timestamp del primer frame del video: 'YYYY-MM-DD HH:MM:SS'")
    p.add_argument("--zonas", default="zonas.json",
        help="Archivo generado por setup_zones.py (default: zonas.json)")
    p.add_argument("--modelo", default="yolov8m.pt",
        help="Modelo YOLO a usar")
    p.add_argument("--guardar-video", action="store_true",
        help="Exportar video anotado para verificacion visual")
    p.add_argument("--output", default="output",
        help="Directorio de salida (default: output/)")
    p.add_argument("--cola-anterior",
        default=None,
        metavar="COLA_JSON",
        help=(
            "Ruta al archivo _v4_cola.json generado por el video anterior. "
            "Permite recuperar el arrival_user real de personas que seguían "
            "esperando al final de ese video usando matching por posición. "
            "Si no se proporciona, el comportamiento es idéntico a v3."
        ),
    )

    args = p.parse_args()

    procesar_video(
        video_path         = args.video,
        zonas_path         = args.zonas,
        timestamp_inicio   = args.timestamp_inicio,
        modelo_nombre      = args.modelo,
        guardar_video      = args.guardar_video,
        output_dir         = args.output,
        cola_anterior_path = args.cola_anterior,
    )


if __name__ == "__main__":
    main()
