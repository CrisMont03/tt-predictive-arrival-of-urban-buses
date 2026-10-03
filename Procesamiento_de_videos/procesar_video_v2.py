"""
procesar_video_v3.py — TT 2026-B050

Variante de procesar_video_v2.py sin el registro de buses cuando no hay
personas esperando en la parada.

Diferencia clave respecto a procesar_video_v2.py:
    v2: cuando un bus llega y no había ninguna persona esperando, añade una
        fila de "solo-bus" al dataset (track_id_persona=None).

    v3: si un bus llega y no había ninguna persona esperando, ese bus
        simplemente no genera fila en el dataset.

La lógica de detección, tracking y filtros de bus es idéntica a v2.

Uso:
    python procesar_video_v3.py <video> \\
        --timestamp-inicio "YYYY-MM-DD HH:MM:SS" \\
        [--zonas zonas.json] \\
        [--modelo yolov8s.pt] \\
        [--guardar-video] \\
        [--output output/]

Ejemplo:
    python procesar_video_v3.py ../Pruebas/videosPrueba/video6AM_recortado.mp4 \\
        --timestamp-inicio "2025-06-02 06:00:00" \\
        --guardar-video
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

# Bus (sin cambios respecto a v1)
MIN_DWELL_BUS_SEG = 0.2
BUS_GAP_TOL_SEG   = 3.0
CONF_BUS_MIN      = 0.30
BUS_RATIO_MIN     = 1.3
BUS_AREA_FRAC     = 0.02 #0.01
BUS_HSV_BAJO      = np.array([15, 25, 140])   # amarillo/crema
BUS_HSV_ALTO      = np.array([32, 180, 255])  #[38, 220, 255])
BUS_COLOR_FRAC    = 0.08

# Verde característico de los buses RTP objetivo
# H: 35–85 cubre verde-amarillo hasta verde puro en escala OpenCV (0–180)
# S: 40–255 excluye blanco/gris que tienen saturación baja
# V: 80–255 excluye sombras muy oscuras
BUS_HSV_VERDE_BAJO   = np.array([35,  40,  80])
BUS_HSV_VERDE_ALTO   = np.array([85, 255, 255])
BUS_COLOR_VERDE_FRAC = 0.008

# Rechazo explícito de buses blancos:
# blanco = saturación muy baja + brillo alto → no es un bus RTP
# Si más del 55 % del recorte es blanco, se descarta
BUS_BLANCO_S_MAX    = 30    # S máxima para considerar un píxel "blanco"
BUS_BLANCO_V_MIN    = 150   # V mínima para considerar un píxel "blanco"
BUS_BLANCO_FRAC_MAX = 0.15  # fracción máxima tolerable de píxeles blancos

# Rechazo explícito de buses morados/azul-oscuro:
# morado en OpenCV HSV: H ≈ 100–160
# Si más del 20 % del recorte es morado, se descarta
BUS_HSV_PURPURA_BAJO  = np.array([100,  50,  50])
BUS_HSV_PURPURA_ALTO  = np.array([160, 255, 255])
BUS_PURPURA_FRAC_MAX  = 0.05
COOLDOWN_BUS_SEG  = 130.0

# Persona: tiempo mínimo en zona para descartar transeúntes
MIN_PRESENCIA_PERSONA_SEG = 1.2

# Cooldown de re-entrada: mismo track_id sale y vuelve antes de este tiempo
# → se trata como salida momentánea del polígono, no como nueva llegada
COOLDOWN_REENTRADA_SEG = 20.0

# ── Recuperación de ID switch (estrategia v2) ─────────────────────────────────
# Ventana de tiempo durante la que se recuerda un track que desapareció de zona.
# Si ByteTrack pierde un ID y lo reasigna dentro de este tiempo, se detecta.
ID_SWITCH_VENTANA_SEG = 25.0

# Distancia máxima entre centroides (px) para considerar que es el mismo individuo.
# Conservador: si la persona no se movió, la distancia debe ser pequeña.
# Ajusta según la resolución y altura de cámara de tu instalación.
ID_SWITCH_DIST_MAX = 80   # px en resolución original del video

# Reset y plausibilidad
RESET_ESPERA_SEG = 15.0
MIN_ESPERA_SEG   = 5.0
MAX_ESPERA_SEG   = 45 * 60


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
    """
    Calcula y devuelve todas las métricas de filtrado para un bbox de bus.
    Útil para verificar qué valores está procesando cada filtro.
    """
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

    # ── Filtros de rechazo (se evalúan primero) ───────────────────────────────
    # Rechazar si la carrocería es predominantemente blanca
    mascara_blanco = (hsv[:, :, 1] < BUS_BLANCO_S_MAX) & (hsv[:, :, 2] > BUS_BLANCO_V_MIN)
    if np.sum(mascara_blanco) / total >= BUS_BLANCO_FRAC_MAX:
        return False

    # Rechazar si la carrocería es predominantemente morada/azul-oscuro
    frac_purpura = np.sum(cv2.inRange(hsv, BUS_HSV_PURPURA_BAJO, BUS_HSV_PURPURA_ALTO) > 0) / total
    if frac_purpura >= BUS_PURPURA_FRAC_MAX:
        return False

    # ── Verificar presencia de colores objetivo (OR) ──────────────────────────
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
# RECUPERACIÓN DE ID SWITCH (estrategia v2)
# ──────────────────────────────────────────────────────────────────────────────

def resolver_tid(
    tid_nuevo:      int,
    bbox:           np.ndarray,
    t_actual:       datetime,
    tracks_perdidos: dict,
    tid_canonico:   dict,
) -> int:
    """
    Devuelve el track_id canónico para tid_nuevo.

    Si tid_nuevo ya fue mapeado anteriormente, retorna directamente su canónico.
    Si no, busca en tracks_perdidos un track que haya desaparecido recientemente
    de la zona y cuyo centroide esté a ≤ ID_SWITCH_DIST_MAX px.
    Si lo encuentra → ID switch confirmado: mapea tid_nuevo → tid_original.
    Si no → tid_nuevo es una persona nueva, retorna tid_nuevo sin mapear.
    """
    # Ya fue resuelto antes
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
# POST-PROCESAMIENTO
# ──────────────────────────────────────────────────────────────────────────────

def calcular_tiempos_espera(
    llegadas_bus: list,
    periodos_persona: list,
) -> list:
    if not llegadas_bus:
        return []

    llegadas_sorted = sorted(llegadas_bus)
    registros = []

    for idx, t_bus in enumerate(llegadas_sorted):
        if idx == 0:
            t_inicio = datetime.min
        else:
            t_inicio = llegadas_sorted[idx - 1] + timedelta(seconds=RESET_ESPERA_SEG)

        for p in periodos_persona:
            t_entrada = p["entrada"]
            t_salida  = p["salida"]

            if t_entrada >= t_bus:
                continue
            if t_salida is not None and t_salida <= t_inicio:
                continue

            t_efectivo = max(t_entrada, t_inicio)
            if t_efectivo >= t_bus:
                continue

            espera_seg = (t_bus - t_efectivo).total_seconds()
            if espera_seg <= 0:
                continue
            if not (MIN_ESPERA_SEG <= espera_seg <= MAX_ESPERA_SEG):
                continue

            registros.append({
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
    video_path:       str,
    zonas_path:       str,
    timestamp_inicio: str,
    modelo_nombre:    str  = "yolov8m.pt",
    guardar_video:    bool = False,
    output_dir:       str  = "output",
) -> None:

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    nombre_base = Path(video_path).stem

    print(f"\n{'='*55}")
    print(f"  Video : {video_path}")
    print(f"  Inicio: {timestamp_inicio}")
    print(f"  Modo  : v3 (track-lifetime deduplication, solo registros con persona)")
    print(f"{'='*55}\n")

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
    bus_zona_metricas:   dict            = {}   # métricas del primer frame del bus activo
    llegadas_bus_confirmadas: list = []
    metricas_txt_lines:  list            = []   # líneas para el .txt de métricas de bus

    frame_dt = FRAME_INTERVAL / fps_real

    # ── Estado: personas ──────────────────────────────────────────────────────
    personas_pendientes: dict = {}   # tid_canonico → datetime (primera detección)
    personas_en_zona:    dict = {}   # tid_canonico → EstadoPersona
    periodos_persona:    list = []
    ultimas_salidas:     dict = {}   # tid_canonico → datetime (última salida)

    # ── Estado: track-lifetime deduplication (v2) ─────────────────────────────
    # Último bbox conocido de cada track_id (actualizado cada frame).
    tracks_ultimo_bbox: dict = {}    # tid_raw → np.ndarray

    # Tracks que desaparecieron recientemente de la zona con su último bbox.
    # Clave: tid_canonico  Valor: (t_desaparicion, bbox)
    tracks_perdidos: dict = {}

    # Mapa de ID switch: tid_raw_nuevo → tid_canonico_original
    tid_canonico: dict = {}

    # Freeze post-bus
    bus_freeze_hasta: datetime | None = None

    ids_persona_prev: set = set()   # tids canónicos del frame anterior

    # ── Video writer ──────────────────────────────────────────────────────────
    writer       = None
    ruta_vid_out = None
    if guardar_video:
        ruta_vid_out = str(Path(output_dir) / f"{nombre_base}_anotado_v2.mp4")
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

            t_actual = frame_a_tiempo(frame_id, fps_real, t_inicio)

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

            # Buses: filtros confianza + geometría + color
            bus_mask = det_all.class_id == CLASE_BUS
            if np.any(bus_mask) and det_all.confidence is not None:
                bus_mask = bus_mask & (det_all.confidence >= CONF_BUS_MIN)
            det_buses = det_all[bus_mask]

            if len(det_buses) > 0:
                bboxes = det_buses.xyxy
                w = bboxes[:, 2] - bboxes[:, 0]
                h = bboxes[:, 3] - bboxes[:, 1]
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

            # ── Actualizar bbox de todos los tracks visibles ──────────────────
            if det_personas.tracker_id is not None:
                for bbox_raw, tid_raw in zip(det_personas.xyxy, det_personas.tracker_id):
                    tracks_ultimo_bbox[int(tid_raw)] = bbox_raw.copy()

            # ── Limpiar tracks_perdidos expirados ─────────────────────────────
            for tid_exp in list(tracks_perdidos):
                t_lost, _ = tracks_perdidos[tid_exp]
                if (t_actual - t_lost).total_seconds() > ID_SWITCH_VENTANA_SEG:
                    del tracks_perdidos[tid_exp]

            # ── Presencia en zona (raw) → resolver a tids canónicos ───────────
            hay_bus_zona  = len(extraer_ids_sin_tracker(det_buses, zona_bus)) > 0
            ids_raw_zona  = extraer_ids_en_zona(det_personas, zona_person)

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
                    # ID switch detectado: mantener bbox bajo el tid canónico
                    tracks_ultimo_bbox[tid_can] = bbox_raw.copy()
                ids_persona_actual.add(tid_can)

            # ════════════════════════════════════════════════════════════════
            # LÓGICA DE BUSES (idéntica a procesar_video.py)
            # ════════════════════════════════════════════════════════════════
            if hay_bus_zona:
                bus_zona_ultimo_det = t_actual
                if not bus_zona_activa:
                    bus_zona_activa     = True
                    bus_zona_entrada    = t_actual
                    bus_zona_confirmado = False
                    bus_zona_dwell_acum = 0.0
                    # Capturar métricas del primer bbox que entró a la zona
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
                                f"  ║  Pasa color (AND) : {'✓' if m.get('pasa_color') else '✗'}\n"
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
            # LÓGICA DE PERSONAS (tids ya canónicos)
            # ════════════════════════════════════════════════════════════════

            # Personas que entran a la zona
            for tid in ids_persona_actual - ids_persona_prev:
                if tid in personas_pendientes or tid in personas_en_zona:
                    continue
                if bus_freeze_hasta is not None and t_actual < bus_freeze_hasta:
                    continue
                ultima = ultimas_salidas.get(tid)
                if ultima and (t_actual - ultima).total_seconds() < COOLDOWN_REENTRADA_SEG:
                    continue
                personas_pendientes[tid] = t_actual

            # Confirmar personas con suficiente tiempo en zona
            for tid in ids_persona_actual:
                if tid not in personas_pendientes:
                    continue
                dwell = (t_actual - personas_pendientes[tid]).total_seconds()
                if dwell < MIN_PRESENCIA_PERSONA_SEG:
                    continue

                t_arr = personas_pendientes.pop(tid)
                personas_en_zona[tid] = EstadoPersona(tid, t_arr)
                tqdm.write(
                    f"  Persona    | track={tid:3d} | "
                    f"llego {t_arr.strftime('%H:%M:%S')}"
                )

            # Personas que salen de la zona
            for tid in ids_persona_prev - ids_persona_actual:
                personas_pendientes.pop(tid, None)
                if tid in personas_en_zona:
                    estado = personas_en_zona.pop(tid)
                    ultimas_salidas[tid] = t_actual
                    periodos_persona.append({
                        "track_id": estado.track_id,
                        "entrada":  estado.tiempo_entrada,
                        "salida":   t_actual,
                    })
                # Guardar en tracks_perdidos para detección de futuros ID switches
                bbox_last = tracks_ultimo_bbox.get(tid)
                if bbox_last is not None:
                    tracks_perdidos[tid] = (t_actual, bbox_last.copy())

            ids_persona_prev = ids_persona_actual.copy()

            # ── Video anotado ─────────────────────────────────────────────────
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

    # Cerrar períodos de personas que seguían en zona al final del video
    for estado in personas_en_zona.values():
        periodos_persona.append({
            "track_id": estado.track_id,
            "entrada":  estado.tiempo_entrada,
            "salida":   None,
        })

    # ── Post-procesamiento ────────────────────────────────────────────────────
    print(f"\n[4/5] Post-proceso ...")
    print(f"      Buses confirmados : {len(llegadas_bus_confirmadas)}")
    print(f"      Periodos de persona: {len(periodos_persona)}")

    registros = calcular_tiempos_espera(llegadas_bus_confirmadas, periodos_persona)

    # Conservar solo la primera aparición de cada track_id_persona
    # (la de arrival_user más temprano) para evitar filas duplicadas.
    vistos: set = set()
    registros_unicos = []
    for r in sorted(registros, key=lambda x: x["arrival_user"]):
        tid = r["track_id_persona"]
        if tid not in vistos:
            vistos.add(tid)
            registros_unicos.append(r)
    registros = registros_unicos

    print(f"      Registros generados: {len(registros)}")

    # ── Exportar ──────────────────────────────────────────────────────────────
    print(f"\n[5/5] Exportando ...")

    csv_path = Path(output_dir) / f"{nombre_base}_v2_dataset.csv"
    CSV_COLS = [
        "track_id_persona", "arrival_user", "bus_arrival",
        "waiting_time_min", "waiting_time_seg",
        "date", "hour", "day", "is_weekend",
    ]
    if registros:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer_csv = csv.DictWriter(f, fieldnames=CSV_COLS)
            writer_csv.writeheader()
            writer_csv.writerows(registros)
        print(f"      Dataset  : {csv_path}")
    else:
        print("      Sin registros en el dataset.")
        print("      Revisa: timestamp correcto, zonas bien definidas, conf suficiente.")

    eventos_path = Path(output_dir) / f"{nombre_base}_v2_eventos.json"
    eventos = {
        "video":            video_path,
        "timestamp_inicio": timestamp_inicio,
        "llegadas_bus": [
            t.strftime("%Y-%m-%d %H:%M:%S") for t in llegadas_bus_confirmadas
        ],
        "llegadas_usuario": [
            {
                "track_id_persona": r["track_id_persona"],
                "arrival_user":     r["arrival_user"],
            }
            for r in registros
        ],
    }
    with open(eventos_path, "w", encoding="utf-8") as f:
        json.dump(eventos, f, indent=2, ensure_ascii=False)
    print(f"      Eventos  : {eventos_path}")

    if metricas_txt_lines:
        metricas_txt_path = Path(output_dir) / f"{nombre_base}_v2_metricas_bus.txt"
        with open(metricas_txt_path, "w", encoding="utf-8") as f:
            f.writelines(metricas_txt_lines)
        print(f"      Métricas : {metricas_txt_path}")

    if ruta_vid_out:
        print(f"      Video    : {ruta_vid_out}")

    if registros:
        tiempos = [r["waiting_time_min"] for r in registros]
        print(f"\n{'='*55}")
        print(f"  Registros totales : {len(registros)}")
        print(f"  Espera promedio   : {sum(tiempos) / len(tiempos):.2f} min")
        print(f"  Espera minima     : {min(tiempos):.2f} min")
        print(f"  Espera maxima     : {max(tiempos):.2f} min")
        print(f"{'='*55}\n")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="TT 2026-B050 | Pipeline v2 — solo registros con persona esperando"
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

    args = p.parse_args()

    procesar_video(
        video_path       = args.video,
        zonas_path       = args.zonas,
        timestamp_inicio = args.timestamp_inicio,
        modelo_nombre    = args.modelo,
        guardar_video    = args.guardar_video,
        output_dir       = args.output,
    )


if __name__ == "__main__":
    main()
