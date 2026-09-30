"""
setup_zones.py — TT 2026-B050

Selector interactivo de zonas (ROI) mediante polígonos.
Ejecutar UNA SOLA VEZ por configuración de cámara. Genera zonas.json.

Controles:
    Clic izquierdo  → agregar vértice
    Clic derecho    → eliminar último vértice
    Enter / Espacio → confirmar polígono (mínimo 3 puntos)
    R               → reiniciar polígono actual
    Q               → cancelar

Uso:
    python setup_zones.py <ruta_video>
"""

import sys
import json
from pathlib import Path

import cv2
import numpy as np


# ──────────────────────────────────────────────────────────────────────────────
# Selector interactivo
# ──────────────────────────────────────────────────────────────────────────────

def seleccionar_poligono(
    frame_base: np.ndarray,
    titulo: str,
    color: tuple,
) -> list:
    """
    Selector interactivo de polígono sobre un frame de video.
    Devuelve lista de (x, y) en las coordenadas del frame recibido.
    """
    puntos = []

    def callback(evento, x, y, flags, param):
        if evento == cv2.EVENT_LBUTTONDOWN:
            puntos.append((x, y))
        elif evento == cv2.EVENT_RBUTTONDOWN and puntos:
            puntos.pop()

    cv2.namedWindow(titulo, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(titulo, 1280, 720)
    cv2.setMouseCallback(titulo, callback)

    linea1 = f"Zona: {titulo}"
    linea2 = "Clic izq: agregar  |  Clic der: borrar  |  Enter: confirmar  |  R: reiniciar  |  Q: salir"

    while True:
        display = frame_base.copy()

        # Barra de instrucciones
        cv2.rectangle(display, (0, 0), (display.shape[1], 68), (30, 30, 30), -1)
        cv2.putText(display, linea1, (10, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2, cv2.LINE_AA)
        cv2.putText(display, linea2, (10, 52),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.50, (200, 200, 200), 1, cv2.LINE_AA)

        if puntos:
            pts = np.array(puntos, dtype=np.int32)

            # Vértices
            for pt in puntos:
                cv2.circle(display, pt, 5, color, -1)

            # Aristas
            if len(puntos) > 1:
                cerrado = len(puntos) > 2
                cv2.polylines(display, [pts], cerrado, color, 2)

            # Relleno semitransparente con ≥ 3 puntos
            if len(puntos) > 2:
                overlay = display.copy()
                cv2.fillPoly(overlay, [pts], color)
                cv2.addWeighted(overlay, 0.20, display, 0.80, 0, display)

            cv2.putText(display, f"Vertices: {len(puntos)}", (10, 92),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)

        cv2.imshow(titulo, display)
        tecla = cv2.waitKey(20) & 0xFF

        if tecla in (13, 32):       # Enter o Espacio → confirmar
            if len(puntos) >= 3:
                break
            else:
                print(f"  Necesitas al menos 3 puntos (tienes {len(puntos)})")
        elif tecla == ord("r"):     # R → reiniciar
            puntos.clear()
        elif tecla == ord("q"):     # Q → salir
            cv2.destroyWindow(titulo)
            print("  Cancelado por el usuario.")
            sys.exit(0)

    cv2.destroyWindow(titulo)
    return puntos


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python setup_zones.py <ruta_video>")
        sys.exit(1)

    video_path  = sys.argv[1]
    output_path = Path("zonas.json")

    # ── Leer primer frame del video ──────────────────────────────────────────
    cap = cv2.VideoCapture(video_path)
    ret, frame = cap.read()
    cap.release()

    if not ret:
        print(f"No se pudo abrir el video: {video_path}")
        sys.exit(1)

    h_orig, w_orig = frame.shape[:2]
    escala = min(1280 / w_orig, 720 / h_orig, 1.0)
    frame_disp = cv2.resize(frame, (int(w_orig * escala), int(h_orig * escala)))

    print(f"\n=== Configuracion de Zonas — TT 2026-B050 ===")
    print(f"  Video      : {video_path}")
    print(f"  Resolucion : {w_orig}x{h_orig}  |  escala display: {escala:.2f}\n")

    # ── PASO 1: ROI_BUS ──────────────────────────────────────────────────────
    print("PASO 1: Define ROI_BUS — zona donde el bus se detiene")
    print("  Traza el poligono que encierra el area de parada del bus.\n")

    pts_bus_disp = seleccionar_poligono(
        frame_disp.copy(),
        "ROI_BUS (zona de llegada del bus)",
        (0, 60, 220),   # rojo
    )
    # Escalar de vuelta a coordenadas originales
    pts_bus_orig = [(int(x / escala), int(y / escala)) for x, y in pts_bus_disp]
    print(f"  ROI_BUS definida: {len(pts_bus_orig)} vertices\n")

    # ── PASO 2: ROI_PERSON ───────────────────────────────────────────────────
    print("PASO 2: Define ROI_PERSON — zona de espera de usuarios")
    print("  Traza el poligono que cubre la acera o parada donde esperan.\n")

    pts_pers_disp = seleccionar_poligono(
        frame_disp.copy(),
        "ROI_PERSON (zona de espera de usuarios)",
        (220, 60, 0),   # azul
    )
    pts_pers_orig = [(int(x / escala), int(y / escala)) for x, y in pts_pers_disp]
    print(f"  ROI_PERSON definida: {len(pts_pers_orig)} vertices\n")

    # ── Guardar JSON ─────────────────────────────────────────────────────────
    zonas = {
        "ROI_BUS":    pts_bus_orig,
        "ROI_PERSON": pts_pers_orig,
        "frame_size": [w_orig, h_orig],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(zonas, f, indent=2)

    print(f"Zonas guardadas en: {output_path.resolve()}\n")

    # ── Verificacion visual ──────────────────────────────────────────────────
    verif = frame.copy()
    bus_pts  = np.array(pts_bus_orig,  dtype=np.int32)
    pers_pts = np.array(pts_pers_orig, dtype=np.int32)

    cv2.polylines(verif, [bus_pts],  True, (0,  60, 220), 3)
    cv2.polylines(verif, [pers_pts], True, (220, 60,  0), 3)

    for pts, label, color in [
        (bus_pts,  "ROI BUS",    (0,  60, 220)),
        (pers_pts, "ROI PERSON", (220, 60,  0)),
    ]:
        cx, cy = np.mean(pts, axis=0).astype(int)
        cv2.putText(verif, label, (cx - 50, cy),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 3, cv2.LINE_AA)

    verif_disp = cv2.resize(verif, (int(w_orig * escala), int(h_orig * escala)))
    cv2.imshow("Verificacion de zonas (cualquier tecla para cerrar)", verif_disp)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

    print("Configuracion completada. Ahora puedes correr procesar_video.py")


if __name__ == "__main__":
    main()
