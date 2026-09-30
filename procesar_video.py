"""
procesar_video.py — TT 2026-B050

Pipeline de deteccion para construir el dataset historico de tiempos de espera.

Flujo:
    Video → Deteccion (YOLOv8, MPS) → Tracking (ByteTrack) →
    Eventos (zonas ROI) → Post-proceso → Dataset CSV

Uso:
    python procesar_video.py <video> \\
        --timestamp-inicio "YYYY-MM-DD HH:MM:SS" \\
        [--zonas zonas.json] \\
        [--modelo yolov8s.pt] \\
        [--guardar-video] \\
        [--output output/]

Ejemplo:
    python procesar_video.py ../Pruebas/videosPrueba/video6AM_recortado.mp4 \\
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

# Clases COCO que usa YOLOv8
CLASE_PERSONA = 0
CLASE_BUS     = 5   # "bus" — clase objetivo (autobús urbano CDMX)
# CLASE_CAMION = 7 eliminado: trailers/trucks generaban falsos positivos

# Procesar 1 de cada N frames  (15 fps ÷ 3 → ~5 fps efectivos)
FRAME_INTERVAL = 3

# Bus: segundos mínimos DENTRO de ROI_BUS (tiempo acumulado real en zona)
# para confirmar que el bus se detuvo (no cuenta el tiempo fuera de zona)
MIN_DWELL_BUS_SEG = 0.2   # 2 frames procesados a 5fps

# Tolerancia de gap: si el bus desaparece del ROI por menos de este tiempo
# (oclusión/fallo puntual de detección) el período no se reinicia
BUS_GAP_TOL_SEG = 3.0

# Confianza mínima para clasificar un objeto como bus
CONF_BUS_MIN = 0.30

# Filtros geométricos para descartar falsos positivos (trailers, caravanas, autos)
# Aspect ratio mínimo (ancho / alto): un bus urbano es claramente más ancho que alto
BUS_RATIO_MIN  = 1.8
# Área mínima como fracción del frame total: descarta detecciones lejanas/pequeñas
BUS_AREA_FRAC  = 0.01   # 1% del frame (ajustar si la cámara está muy lejos)

# Filtro de color: rango HSV del amarillo/crema del bus objetivo
# OpenCV usa H en [0,180]; el bus objetivo tiene carrocería amarillo-dorada
BUS_HSV_BAJO   = np.array([15, 25, 140])   # H≈30°  S bajo   V medio-alto
BUS_HSV_ALTO   = np.array([38, 220, 255])  # H≈76°  S alto   V máximo
# Fracción mínima de píxeles con ese color en la zona de carrocería (mitad superior)
BUS_COLOR_FRAC = 0.08                      # 8% — leniente para distintas iluminaciones

# Verde característico de los buses RTP objetivo
# H: 35–85 cubre verde-amarillo hasta verde puro en escala OpenCV (0–180)
# S: 40–255 excluye blanco/gris que tienen saturación baja
# V: 80–255 excluye sombras muy oscuras
BUS_HSV_VERDE_BAJO   = np.array([35,  40,  80])
BUS_HSV_VERDE_ALTO   = np.array([85, 255, 255])
BUS_COLOR_VERDE_FRAC = 0.05

# Rechazo explícito de buses blancos:
# blanco = saturación muy baja + brillo alto → no es un bus RTP
# Si más del 55 % del recorte es blanco, se descarta
BUS_BLANCO_S_MAX    = 30    # S máxima para considerar un píxel "blanco"
BUS_BLANCO_V_MIN    = 150   # V mínima para considerar un píxel "blanco"
BUS_BLANCO_FRAC_MAX = 0.55  # fracción máxima tolerable de píxeles blancos

# Rechazo explícito de buses morados/azul-oscuro:
# morado en OpenCV HSV: H ≈ 100–160
# Si más del 20 % del recorte es morado, se descarta
BUS_HSV_PURPURA_BAJO  = np.array([100,  50,  50])
BUS_HSV_PURPURA_ALTO  = np.array([160, 255, 255])
BUS_PURPURA_FRAC_MAX  = 0.20

# Cooldown entre confirmaciones de bus (evita doble conteo del mismo bus
# si su track_id cambia mientras está detenido)
COOLDOWN_BUS_SEG = 90.0

# Persona: segundos mínimos en ROI_PERSON para no contar transeúntes
MIN_PRESENCIA_PERSONA_SEG = 1.2

# Cooldown de re-entrada: si un track_id sale y vuelve a entrar antes de este
# tiempo, se ignora (evita doble conteo por salidas momentáneas del polígono)
COOLDOWN_REENTRADA_SEG = 20.0

# Deduplicación espacial de personas: si se confirma una nueva persona cuyo bbox
# se superpone (IoU ≥ IOU_MIN) con otra confirmada en los últimos VENTANA_SEG
# segundos, se considera el mismo individuo re-detectado y se descarta.
PERSONA_IOU_VENTANA_SEG = 50.0
PERSONA_IOU_MIN         = 0.5

# Reset de timer para personas que no abordaron el bus (CLAUDE.md: +15s)
RESET_ESPERA_SEG = 15.0

# Filtro de plausibilidad en tiempos de espera
MIN_ESPERA_SEG = 5.0
MAX_ESPERA_SEG = 45 * 60   # 45 minutos


# ──────────────────────────────────────────────────────────────────────────────
# DATACLASSES
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class EstadoBus:
    """Registro de un bus dentro de ROI_BUS."""
    track_id:       int
    tiempo_entrada: datetime
    confirmado:     bool = False


@dataclass
class EstadoPersona:
    """Persona confirmada en la zona de espera."""
    track_id:       int
    tiempo_entrada: datetime    # timestamp de llegada (puede ser reiniciado)


# ──────────────────────────────────────────────────────────────────────────────
# UTILIDADES
# ──────────────────────────────────────────────────────────────────────────────

def cargar_zonas(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_timestamp(texto: str) -> datetime:
    return datetime.strptime(texto.strip(), "%Y-%m-%d %H:%M:%S")


def frame_a_tiempo(frame_id: int, fps: float, inicio: datetime) -> datetime:
    return inicio + timedelta(seconds=frame_id / fps)



def es_bus_objetivo(frame: np.ndarray, bbox) -> bool:
    """
    Verifica que el bbox contenga el color amarillo/crema del bus objetivo.
    Analiza la mitad superior del recorte (carrocería) para evitar ruido
    de ruedas, asfalto o personas en la parte baja.
    """
    x1, y1, x2, y2 = map(int, bbox)
    x1 = max(x1, 0); y1 = max(y1, 0)
    x2 = min(x2, frame.shape[1]); y2 = min(y2, frame.shape[0])
    alto = y2 - y1
    if alto < 10 or (x2 - x1) < 10:
        return False
    # Solo la mitad superior (zona de carrocería)
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

    return frac_amarillo >= BUS_COLOR_FRAC or frac_verde >= BUS_COLOR_VERDE_FRAC


def calc_iou(b1, b2) -> float:
    """Intersection over Union entre dos bboxes [x1,y1,x2,y2]."""
    xi1 = max(b1[0], b2[0]); yi1 = max(b1[1], b2[1])
    xi2 = min(b1[2], b2[2]); yi2 = min(b1[3], b2[3])
    inter = max(0.0, xi2 - xi1) * max(0.0, yi2 - yi1)
    a1    = (b1[2] - b1[0]) * (b1[3] - b1[1])
    a2    = (b2[2] - b2[0]) * (b2[3] - b2[1])
    union = a1 + a2 - inter
    return inter / (union + 1e-6) if union > 0 else 0.0


def es_persona_duplicada(
    bbox,
    posiciones_recientes: list,
    t_actual: datetime,
) -> bool:
    """
    Retorna True si el bbox se superpone con una persona recientemente
    confirmada. Detecta re-registros causados por cambios de track_id
    (ID switch de ByteTrack) cuando la persona física no se movió.
    """
    for t_reg, bbox_reg in posiciones_recientes:
        if (t_actual - t_reg).total_seconds() > PERSONA_IOU_VENTANA_SEG:
            continue
        if calc_iou(bbox, bbox_reg) >= PERSONA_IOU_MIN:
            return True
    return False


def seleccionar_device() -> str:
    """Selecciona el mejor device disponible: MPS (Apple Silicon) > CUDA > CPU."""
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def extraer_ids_sin_tracker(det: sv.Detections, zona: sv.PolygonZone) -> set:
    """Igual que extraer_ids_en_zona pero usando el índice como ID (sin tracker_id)."""
    if len(det) == 0:
        return set()
    mascara = zona.trigger(detections=det)
    if not np.any(mascara):
        return set()
    return set(int(i) for i in np.where(mascara)[0])


def extraer_ids_en_zona(det: sv.Detections, zona: sv.PolygonZone) -> set:
    """Devuelve el conjunto de track_ids activos dentro del polígono."""
    if len(det) == 0 or det.tracker_id is None:
        return set()
    mascara = zona.trigger(detections=det)
    if not np.any(mascara):
        return set()
    return set(int(tid) for tid in det.tracker_id[mascara])


def puede_confirmar_bus(t: datetime, llegadas: list) -> bool:
    """True si han pasado COOLDOWN_BUS_SEG desde la última confirmación."""
    if not llegadas:
        return True
    return (t - max(llegadas)).total_seconds() >= COOLDOWN_BUS_SEG


# ──────────────────────────────────────────────────────────────────────────────
# POST-PROCESAMIENTO: cálculo de tiempos de espera
# ──────────────────────────────────────────────────────────────────────────────

def calcular_tiempos_espera(
    llegadas_bus: list,
    periodos_persona: list,
) -> list:
    """
    Para cada intervalo entre buses consecutivos calcula el waiting_time
    de cada persona que estuvo presente en ese intervalo.

    Lógica (CLAUDE.md + RegistroManual.md):
        intervalo[i] empieza en: bus[i-1].arrival + RESET_ESPERA_SEG
        intervalo[i] termina en: bus[i].arrival

        arrival_efectivo = max(persona.entrada, inicio_intervalo)
        waiting_time     = bus[i].arrival - arrival_efectivo

    Cubre automáticamente:
        - Persona que espera y aborda el bus           → waiting_time normal
        - "Se forma y se va" (sale antes del bus)      → waiting_time calculado igual
        - "No tomará el bus" (se queda)                → timer reiniciado en siguiente ciclo
    """
    if not llegadas_bus:
        return []

    llegadas_sorted = sorted(llegadas_bus)
    registros = []

    for idx, t_bus in enumerate(llegadas_sorted):

        # Inicio del intervalo (primer bus no tiene anterior)
        if idx == 0:
            t_inicio = datetime.min
        else:
            t_inicio = llegadas_sorted[idx - 1] + timedelta(seconds=RESET_ESPERA_SEG)

        for p in periodos_persona:
            t_entrada = p["entrada"]
            t_salida  = p["salida"]     # None → sigue en zona al final del video

            # La persona debe haber llegado ANTES que el bus
            if t_entrada >= t_bus:
                continue

            # La persona debe haber estado en zona durante este intervalo
            if t_salida is not None and t_salida <= t_inicio:
                continue

            # Arrival efectivo para este intervalo
            t_efectivo = max(t_entrada, t_inicio)
            if t_efectivo >= t_bus:
                continue

            espera_seg = (t_bus - t_efectivo).total_seconds()
            # Garantía explícita: user_arrival siempre antes de bus_arrival
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
# VISUALIZACIÓN (solo cuando --guardar-video está activo)
# ──────────────────────────────────────────────────────────────────────────────

def dibujar_frame(
    frame: np.ndarray,
    det_buses: sv.Detections,
    det_personas: sv.Detections,
    roi_bus_pts: np.ndarray,
    roi_person_pts: np.ndarray,
    t_actual: datetime,
    n_buses: int,
    n_espera: int,
) -> np.ndarray:
    """Dibuja bounding boxes, zonas y panel de información sobre el frame."""
    out = frame.copy()

    # Polígonos de zonas
    cv2.polylines(out, [roi_bus_pts],    True, (0,  60, 220), 2)
    cv2.polylines(out, [roi_person_pts], True, (220, 60,  0), 2)

    # Bounding boxes de buses (sin tracker_id — detección directa YOLO)
    if len(det_buses) > 0:
        for i, bbox in enumerate(det_buses.xyxy):
            x1, y1, x2, y2 = map(int, bbox)
            cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 0), 2)
            conf = det_buses.confidence[i] if det_buses.confidence is not None else 0
            cv2.putText(out, f"BUS {conf:.2f}", (x1, max(y1 - 6, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 0), 2, cv2.LINE_AA)

    # Bounding boxes de personas
    if len(det_personas) > 0 and det_personas.tracker_id is not None:
        for bbox, tid in zip(det_personas.xyxy, det_personas.tracker_id):
            x1, y1, x2, y2 = map(int, bbox)
            cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 200), 2)
            cv2.putText(out, f"P{tid}", (x1, max(y1 - 4, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 200), 1, cv2.LINE_AA)

    # Panel de información (fondo semiopaco)
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
    modelo_nombre:    str  = "yolov8s.pt",
    guardar_video:    bool = False,
    output_dir:       str  = "output",
) -> None:

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    nombre_base = Path(video_path).stem

    # ── 1. Configuración ─────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"  Video : {video_path}")
    print(f"  Inicio: {timestamp_inicio}")
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

    # ── 2. Modelo YOLOv8 (MPS en M2) ────────────────────────────────────────
    print(f"[1/5] Cargando {modelo_nombre}  (device={device}) ...")
    modelo = YOLO(modelo_nombre)
    print(f"      Listo.\n")

    # ── 3. Metadata del video ────────────────────────────────────────────────
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

    # ── 4. Trackers ByteTrack (separados por clase) ──────────────────────────
    buf_bus  = max(int(fps_efectivo * 8),  5)   # memoria de 8s para buses
    buf_pers = max(int(fps_efectivo * 10), 15)  # memoria de 10s para personas
                                                 # (evita nuevo ID por oclusión breve)

    # Buses: NO se usa ByteTrack (aparecen en frames muy dispersos → el tracker
    # nunca confirma el track). Se usa detección directa YOLO + zona geométrica.
    tracker_persona = sv.ByteTrack(
        track_activation_threshold = 0.30,
        lost_track_buffer          = buf_pers,
        minimum_matching_threshold = 0.60,  # más permisivo → re-asocia mejor
        frame_rate                 = int(fps_efectivo),
        minimum_consecutive_frames = 3,
    )

    # ── 5. Estructuras de estado ─────────────────────────────────────────────
    # Buses (tracker-free: estado booleano de presencia en zona)
    bus_zona_activa:     bool            = False
    bus_zona_entrada:    datetime | None = None   # timestamp de primera entrada al período
    bus_zona_confirmado: bool            = False
    bus_zona_ultimo_det: datetime | None = None   # último frame con bus en zona
    bus_zona_dwell_acum: float           = 0.0    # segundos REALES dentro de zona (no incluye gaps)
    llegadas_bus_confirmadas: list = []   # list[datetime]

    # Duración nominal de un frame procesado (para acumular dwell correctamente)
    frame_dt = FRAME_INTERVAL / fps_real

    # Personas (dos fases):
    #   pendientes  → detectadas pero no confirmadas aún (< MIN_PRESENCIA_SEG)
    #   confirmadas → en zona con arrival_time registrado
    personas_pendientes: dict  = {}   # track_id → datetime (primera detección)
    personas_en_zona:    dict  = {}   # track_id → EstadoPersona
    periodos_persona:    list  = []   # personas que ya salieron de zona
    ultimas_salidas:     dict  = {}   # track_id → datetime (última salida de zona)

    # Deduplicación espacial: historial de bboxes de personas confirmadas
    posiciones_personas_recientes: list = []   # list[(datetime, np.ndarray)]

    # Freeze post-bus: no registrar nuevas llegadas hasta este timestamp
    bus_freeze_hasta: datetime | None = None

    ids_persona_prev: set = set()

    # ── 6. Video writer (opcional) ───────────────────────────────────────────
    writer     = None
    ruta_vid_out = None
    if guardar_video:
        ruta_vid_out = str(Path(output_dir) / f"{nombre_base}_anotado.mp4")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(ruta_vid_out, fourcc, fps_efectivo, (w_vid, h_vid))

    # ── 7. Loop principal ────────────────────────────────────────────────────
    print("[3/5] Procesando frames...\n")
    cap      = cv2.VideoCapture(video_path)
    frame_id = 0

    with tqdm(total=frames_proc, unit="frame", ncols=72) as pbar:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            # Saltar frames no procesados
            if frame_id % FRAME_INTERVAL != 0:
                frame_id += 1
                continue

            t_actual = frame_a_tiempo(frame_id, fps_real, t_inicio)

            # ── Detección YOLOv8 ────────────────────────────────────────────
            resultado = modelo(
                frame,
                device  = device,
                classes = [CLASE_PERSONA, CLASE_BUS],
                conf    = 0.25,
                imgsz   = 1280,   # resolución de inferencia (default 640)
                verbose = False,
            )[0]

            det_all = sv.Detections.from_ultralytics(resultado)

            # Buses: solo clase 5 (bus), con umbral de confianza propio
            bus_mask = det_all.class_id == CLASE_BUS
            if np.any(bus_mask) and det_all.confidence is not None:
                bus_mask = bus_mask & (det_all.confidence >= CONF_BUS_MIN)
            det_buses = det_all[bus_mask]

            # Filtros geométricos: aspect ratio y área mínima
            if len(det_buses) > 0:
                bboxes = det_buses.xyxy
                w = bboxes[:, 2] - bboxes[:, 0]
                h = bboxes[:, 3] - bboxes[:, 1]
                ratio = w / (h + 1e-6)
                area  = w * h
                area_min = w_vid * h_vid * BUS_AREA_FRAC
                det_buses = det_buses[(ratio >= BUS_RATIO_MIN) & (area >= area_min)]

            # Filtro de color: conservar solo detecciones con color amarillo/crema
            if len(det_buses) > 0:
                color_mask = np.array([
                    es_bus_objetivo(frame, bbox) for bbox in det_buses.xyxy
                ])
                det_buses = det_buses[color_mask]
            det_personas = det_all[det_all.class_id == CLASE_PERSONA]

            # ── Tracking ByteTrack (solo personas) ───────────────────────────
            det_personas = tracker_persona.update_with_detections(det_personas)

            # ── Presencia en zona ────────────────────────────────────────────
            hay_bus_zona       = len(extraer_ids_sin_tracker(det_buses, zona_bus)) > 0
            ids_persona_actual = extraer_ids_en_zona(det_personas, zona_person)

            # ════════════════════════════════════════════════════════════════
            # LÓGICA DE BUSES (tracker-free)
            # ════════════════════════════════════════════════════════════════
            if hay_bus_zona:
                bus_zona_ultimo_det = t_actual
                if not bus_zona_activa:
                    # Bus entra a la zona por primera vez (o tras un gap largo)
                    bus_zona_activa     = True
                    bus_zona_entrada    = t_actual
                    bus_zona_confirmado = False
                    bus_zona_dwell_acum = 0.0
                elif not bus_zona_confirmado:
                    # Acumular solo el tiempo real en zona (frame a frame)
                    bus_zona_dwell_acum += frame_dt
                    if bus_zona_dwell_acum >= MIN_DWELL_BUS_SEG:
                        if puede_confirmar_bus(t_actual, llegadas_bus_confirmadas):
                            bus_zona_confirmado = True
                            # Guardar t_actual (confirmación) como bus_arrival:
                            # garantiza que solo personas que llegaron ANTES del
                            # bus confirmado sean asociadas en el post-proceso.
                            llegadas_bus_confirmadas.append(t_actual)
                            tqdm.write(
                                f"  Bus LLEGO  | "
                                f"{t_actual.strftime('%H:%M:%S')}"
                            )
            else:
                # Bus fuera de zona: resetear solo si el gap supera la tolerancia
                if bus_zona_activa and bus_zona_ultimo_det is not None:
                    gap = (t_actual - bus_zona_ultimo_det).total_seconds()
                    if gap > BUS_GAP_TOL_SEG:
                        if bus_zona_confirmado:
                            # Bus confirmado que acaba de irse: activar freeze de 15s
                            # para no registrar nuevas llegadas mientras la gente aborda
                            bus_freeze_hasta = t_actual + timedelta(seconds=RESET_ESPERA_SEG)
                            personas_pendientes.clear()   # descartar pendientes del período del bus
                        bus_zona_activa     = False
                        bus_zona_entrada    = None
                        bus_zona_confirmado = False
                        bus_zona_dwell_acum = 0.0

            # ════════════════════════════════════════════════════════════════
            # LÓGICA DE PERSONAS
            # ════════════════════════════════════════════════════════════════

            # Personas que entran a la zona (fase 1: pendientes)
            for tid in ids_persona_actual - ids_persona_prev:
                if tid not in personas_pendientes and tid not in personas_en_zona:
                    # No registrar durante el freeze post-bus
                    if bus_freeze_hasta is not None and t_actual < bus_freeze_hasta:
                        continue
                    # Ignorar re-entradas dentro del cooldown (salida momentánea
                    # del polígono o fragmentación de ID del tracker)
                    ultima = ultimas_salidas.get(tid)
                    if ultima and (t_actual - ultima).total_seconds() < COOLDOWN_REENTRADA_SEG:
                        continue
                    personas_pendientes[tid] = t_actual

            # Personas que siguen en zona → confirmar si cumplen tiempo mínimo
            for tid in ids_persona_actual:
                if tid in personas_pendientes:
                    dwell = (t_actual - personas_pendientes[tid]).total_seconds()
                    if dwell >= MIN_PRESENCIA_PERSONA_SEG:
                        # Obtener bbox actual para deduplicación espacial
                        bbox_pers = None
                        if det_personas.tracker_id is not None:
                            idx_p = np.where(det_personas.tracker_id == tid)[0]
                            if len(idx_p) > 0:
                                bbox_pers = det_personas.xyxy[idx_p[0]]

                        # Descartar si es la misma persona física re-detectada
                        if bbox_pers is not None and es_persona_duplicada(
                            bbox_pers, posiciones_personas_recientes, t_actual
                        ):
                            personas_pendientes.pop(tid)
                            continue

                        t_arr = personas_pendientes.pop(tid)
                        personas_en_zona[tid] = EstadoPersona(tid, t_arr)
                        if bbox_pers is not None:
                            # Limpiar entradas antiguas y registrar posición
                            posiciones_personas_recientes[:] = [
                                (t, b) for t, b in posiciones_personas_recientes
                                if (t_actual - t).total_seconds() <= PERSONA_IOU_VENTANA_SEG
                            ]
                            posiciones_personas_recientes.append((t_actual, bbox_pers.copy()))
                        tqdm.write(
                            f"  Persona    | track={tid:3d} | "
                            f"llego {t_arr.strftime('%H:%M:%S')}"
                        )

            # Personas que salen de la zona → cerrar su período
            for tid in ids_persona_prev - ids_persona_actual:
                personas_pendientes.pop(tid, None)   # nunca confirmada → ignorar
                if tid in personas_en_zona:
                    estado = personas_en_zona.pop(tid)
                    ultimas_salidas[tid] = t_actual   # registrar tiempo de salida
                    periodos_persona.append({
                        "track_id": estado.track_id,
                        "entrada":  estado.tiempo_entrada,
                        "salida":   t_actual,
                    })

            # ── Actualizar conjuntos del frame anterior ──────────────────────
            ids_persona_prev = ids_persona_actual.copy()

            # ── Video anotado (opcional) ─────────────────────────────────────
            if writer is not None:
                frame_out = dibujar_frame(
                    frame, det_buses, det_personas,   # det_buses = YOLO sin tracker
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
            "salida":   None,   # fin de video
        })

    # ── 8. Post-procesamiento ────────────────────────────────────────────────
    print(f"\n[4/5] Post-proceso ...")
    print(f"      Buses confirmados : {len(llegadas_bus_confirmadas)}")
    print(f"      Periodos de persona: {len(periodos_persona)}")

    registros = calcular_tiempos_espera(llegadas_bus_confirmadas, periodos_persona)

    # Conservar solo la primera aparición de cada track_id_persona
    # (la de arrival_user más temprano) para evitar filas duplicadas
    vistos: set = set()
    registros_unicos = []
    for r in sorted(registros, key=lambda x: x["arrival_user"]):
        if r["track_id_persona"] not in vistos:
            vistos.add(r["track_id_persona"])
            registros_unicos.append(r)
    registros = registros_unicos

    print(f"      Registros generados: {len(registros)}")

    # ── 9. Exportar resultados ───────────────────────────────────────────────
    print(f"\n[5/5] Exportando ...")

    # Dataset CSV
    csv_path = Path(output_dir) / f"{nombre_base}_dataset.csv"
    if registros:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer_csv = csv.DictWriter(f, fieldnames=list(registros[0].keys()))
            writer_csv.writeheader()
            writer_csv.writerows(registros)
        print(f"      Dataset  : {csv_path}")
    else:
        print("      Sin registros en el dataset.")
        print("      Revisa: timestamp correcto, zonas bien definidas, conf suficiente.")

    # Eventos JSON: llegadas de buses + llegadas de usuarios
    eventos_path = Path(output_dir) / f"{nombre_base}_eventos.json"
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

    if ruta_vid_out:
        print(f"      Video    : {ruta_vid_out}")

    # ── Resumen ──────────────────────────────────────────────────────────────
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
        description="TT 2026-B050 | Pipeline de deteccion de tiempos de espera"
    )
    p.add_argument("video",
        help="Ruta al video de vigilancia")
    p.add_argument("--timestamp-inicio", required=True,
        help="Timestamp del primer frame del video: 'YYYY-MM-DD HH:MM:SS'. "
             "Léelo del overlay visual en el primer frame.")
    p.add_argument("--zonas", default="zonas.json",
        help="Archivo generado por setup_zones.py (default: zonas.json)")
    p.add_argument("--modelo", default="yolov8m.pt",
        help="Modelo YOLO a usar. yolov8m.pt (recomendado) o yolov8s.pt (más rápido)")
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
