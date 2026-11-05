"""
consolidar_dataset.py — TT 2026-B050

Unifica todos los CSVs individuales generados por procesar_video_v4.py
en un único dataset maestro, lo guarda en SQLite y lo prepara para
el enriquecimiento posterior con variables contextuales.

USO
───
# 1. Consolidar todos los CSVs v4 en la BD:
    python consolidar_dataset.py consolidar

# 2. Enriquecer con clima (Open-Meteo, gratis, sin API key) y días festivos:
    python consolidar_dataset.py enriquecer

# 3. Exportar el dataset final como CSV listo para el modelo:
    python consolidar_dataset.py exportar

NOTAS
─────
- Se toman CSVs *_v4_dataset.csv de Codigo/Procesamiento_de_videos/output/dataset
  generados por procesar_video_v4.py (una carpeta por franja horaria).
- La base y el CSV consolidado se guardan en Codigo/Dataset/output
- Las tablas en SQLite son:
    · eventos_espera       → datos base generados por el pipeline de visión (inmutables)
    · eventos_enriquecidos → variables contextuales añadidas después (clima + festivos)
  La separación en dos tablas garantiza que un error en el enriquecimiento
  nunca corrompa los datos base; eventos_enriquecidos puede borrarse y
  regenerarse por completo sin tocar los tiempos de espera.
- Clima obtenido de Open-Meteo Historical API (https://open-meteo.com), sin registro ni costo.
  Una sola petición por fecha trae las 24 horas → muy eficiente.
- Días festivos calculados con la librería 'holidays' (sin conexión a internet),
  cubre los 7 festivos oficiales de México (Ley Federal del Trabajo, Art. 74).
"""

import argparse
import csv
import sqlite3
import math
import hashlib
from datetime import datetime, date
from pathlib import Path
import sys

# ─── Dependencias opcionales ────────────────────────────────────────────────
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import holidays
    HAS_HOLIDAYS = True
except ImportError:
    HAS_HOLIDAYS = False


# ─── Rutas ───────────────────────────────────────────────────────────────────
RAIZ          = Path(__file__).resolve().parent
RESULTADOS    = RAIZ.parent / "Procesamiento_de_videos" / "output" / "dataset"
DB_PATH       = RAIZ / "output" / "dataset_maestro.db"
CSV_FINAL     = RAIZ / "output" / "dataset_final.csv"
STATION_ID    = "rio_consulado"
CAMERA_ID     = "rio_consulado_cctv_1"
SELECTED_DATES = None
WEATHER_CACHE = None
INCLUDE_GENERATED = False
SYNTHETIC_DATES = set()

# Coordenadas de la estación Río Consulado – Misterios (CDMX)
ESTACION_LAT  = 19.4624
ESTACION_LON  = -99.1297


# ─── Esquema SQLite ──────────────────────────────────────────────────────────
SQL_CREAR_TABLA_BASE = """
CREATE TABLE IF NOT EXISTS eventos_espera (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    video_source        TEXT    NOT NULL,   -- nombre del video de origen (ej. "Video6AM")
    track_id_persona    INTEGER,            -- ID de tracking asignado por ByteTrack
    arrival_user        TEXT    NOT NULL,   -- timestamp de llegada de la persona (YYYY-MM-DD HH:MM:SS)
    bus_arrival         TEXT    NOT NULL,   -- timestamp del bus asociado          (YYYY-MM-DD HH:MM:SS)
    waiting_time_min    REAL    NOT NULL,   -- tiempo de espera en minutos (bus_arrival - arrival_user)
    waiting_time_seg    REAL    NOT NULL,   -- tiempo de espera en segundos
    date                TEXT    NOT NULL,   -- fecha del evento (YYYY-MM-DD)
    hour                INTEGER NOT NULL,   -- hora del evento (0-23)
    day                 TEXT    NOT NULL,   -- día de la semana en inglés (Monday … Sunday)
    is_weekend          INTEGER NOT NULL    -- 0 = entre semana, 1 = fin de semana
);
"""

SQL_CREAR_TABLA_ENRIQUECIDA = """
CREATE TABLE IF NOT EXISTS eventos_enriquecidos (
    id                  INTEGER PRIMARY KEY,  -- mismo id que eventos_espera (relación 1:1)
    precipitation_mm    REAL,       -- precipitación en mm (continuo; derivar binario con > 0 si se necesita)
    temp_c              REAL,       -- temperatura a 2 m en °C (Open-Meteo: temperature_2m)
    humidity            INTEGER,    -- humedad relativa 0-100 % (Open-Meteo: relative_humidity_2m)
    is_holiday          INTEGER     -- 0 = día normal, 1 = festivo oficial México (Ley Federal del Trabajo)
);
"""

# ─── Columnas del CSV generado por procesar_video_v4.py ─────────────────────
COLS_CSV = [
    "track_id_persona", "arrival_user", "bus_arrival",
    "waiting_time_min", "waiting_time_seg",
    "date", "hour", "day", "is_weekend",
]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def conectar_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def inicializar_db(conn: sqlite3.Connection) -> None:
    conn.execute(SQL_CREAR_TABLA_BASE)
    conn.execute(SQL_CREAR_TABLA_ENRIQUECIDA)
    conn.execute("""CREATE TABLE IF NOT EXISTS eventos_procedencia (
        id INTEGER PRIMARY KEY REFERENCES eventos_espera(id),
        source_file TEXT NOT NULL, source_line INTEGER NOT NULL,
        source_sha256 TEXT NOT NULL, is_synthetic INTEGER NOT NULL CHECK(is_synthetic IN (0,1))
    )""")
    # Do not remove old duplicates silently; a conflicting database needs review.
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS eventos_origen_unique
        ON eventos_espera(video_source, arrival_user, bus_arrival, track_id_persona)""")
    conn.commit()


def encontrar_csvs_v2() -> list[Path]:
    """
    Busca recursivamente todos los archivos *_v4_dataset.csv bajo RESULTADOS.
    Estructura esperada: Dataset/Dataset/YYYY-MM-DD/{hora}/Video{HORA}_v4_dataset.csv
    Cada archivo corresponde a una franja horaria procesada por procesar_video_v4.py.
    """
    return daily_csvs(RESULTADOS, SELECTED_DATES)


def daily_csvs(root, dates=None):
    """Only daily dataset/datasets CSVs; never recurse into auxiliary folders."""
    selected = set(dates) if dates is not None else None
    found = []
    for day in sorted(Path(root).glob("????_??_??")):
        try:
            date_key = datetime.strptime(day.name, "%Y_%m_%d").date().isoformat()
        except ValueError:
            continue
        if not day.is_dir() or (selected is not None and date_key not in selected):
            continue
        for folder in ("dataset", "datasets"):
            found.extend((day / folder).glob("*_v4_dataset.csv"))
            if INCLUDE_GENERATED:
                found.extend(p for p in (day / folder).glob("*_dataset.csv")
                             if not p.name.endswith("_v4_dataset.csv"))
    return sorted(found)


def validate_row(row, path, index):
    """Existing numeric/time checks, with source location on every failure."""
    try:
        track = int(row["track_id_persona"])
        minutes, seconds = float(row["waiting_time_min"]), float(row["waiting_time_seg"])
        start, end = datetime.fromisoformat(row["arrival_user"]), datetime.fromisoformat(row["bus_arrival"])
        if track < -1 or not all(math.isfinite(v) and v >= 0 for v in (minutes, seconds)) or end < start:
            raise ValueError("Evento inválido")
        if track == -1 and (minutes != 0 or seconds != 0 or start != end):
            raise ValueError("Centinela inválido")
        if track >= 0 and abs((end - start).total_seconds() / 60 - minutes) > .1:
            raise ValueError("Espera/timestamp inconsistente")
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"{error}: {path}:{index}") from error


def leer_csv(path: Path) -> list[dict]:
    filas = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not set(COLS_CSV).issubset(reader.fieldnames or []):
            raise ValueError(f"Columnas incompletas: {path}")
        for index, row in enumerate(reader, 2):
            validate_row(row, path, index)
            filas.append(row)
    return filas


# ─────────────────────────────────────────────────────────────────────────────
# COMANDO: consolidar
# ─────────────────────────────────────────────────────────────────────────────

def cmd_consolidar() -> None:
    """
    Lee todos los CSVs v4, elimina duplicados y los inserta en la tabla
    eventos_espera de SQLite.

    Cada fila representa el intervalo de espera de una persona en la parada,
    calculado por procesar_video_v4.py como: waiting_time = bus_arrival - arrival_user.

    Deduplicación: se considera duplicado un registro con la misma combinación
    (video_source, arrival_user, bus_arrival, track_id_persona) ya existente en la BD.
    Esto permite re-ejecutar la consolidación de forma segura si se agrega
    o reprocesa un video, sin generar filas repetidas.
    """
    conn = conectar_db()
    inicializar_db(conn)

    csvs = encontrar_csvs_v2()
    if not csvs:
        print(f"[!] No se encontraron CSVs en {RESULTADOS}")
        print("    Verifica que la ruta sea correcta y que existan archivos *_v4_dataset.csv")
        return

    print(f"\n{'='*60}")
    print(f"  CSVs encontrados: {len(csvs)}")
    print(f"  Base de datos   : {DB_PATH}")
    print(f"{'='*60}\n")

    total_insertados = 0
    total_omitidos   = 0

    for csv_path in csvs:
        filas = leer_csv(csv_path)
        video_source = csv_path.stem.replace("_v4_dataset", "")  # e.g. "Video6AM"

        insertados = 0
        omitidos   = 0

        source_sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()
        is_synthetic = csv_path.parent.parent.name.replace("_", "-") in SYNTHETIC_DATES
        if INCLUDE_GENERATED and not csv_path.name.endswith("_v4_dataset.csv") and not is_synthetic:
            raise ValueError(f"CSV generado sin fecha sintética declarada: {csv_path}")
        for line, fila in enumerate(filas, 2):
            # Verificar si ya existe para evitar duplicados al re-ejecutar
            existe = conn.execute(
                """
                SELECT id FROM eventos_espera
                WHERE video_source = ? AND arrival_user = ? AND bus_arrival = ? AND track_id_persona = ?
                """,
                (video_source, fila["arrival_user"], fila["bus_arrival"], fila["track_id_persona"]),
            ).fetchone()

            if existe:
                event_id = existe[0]
                previous = conn.execute("SELECT source_sha256,is_synthetic FROM eventos_procedencia WHERE id=?", (event_id,)).fetchone()
                if previous and (previous[0] != source_sha or previous[1] != int(is_synthetic)):
                    raise ValueError(f"Origen modificado; usa una base nueva: {csv_path}")
                conn.execute("INSERT OR IGNORE INTO eventos_procedencia VALUES(?,?,?,?,?)",
                             (event_id, str(csv_path.resolve()), line, source_sha, int(is_synthetic)))
                omitidos += 1
                continue

            cursor = conn.execute(
                """
                INSERT INTO eventos_espera
                    (video_source, track_id_persona, arrival_user, bus_arrival,
                     waiting_time_min, waiting_time_seg, date, hour, day, is_weekend)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_source,
                    int(fila["track_id_persona"]) if fila["track_id_persona"] else None,
                    fila["arrival_user"],
                    fila["bus_arrival"],
                    float(fila["waiting_time_min"]),
                    float(fila["waiting_time_seg"]),
                    fila["date"],
                    int(fila["hour"]),
                    fila["day"],
                    1 if str(fila["is_weekend"]).lower() in ("true", "1") else 0,
                ),
            )
            conn.execute("INSERT INTO eventos_procedencia VALUES(?,?,?,?,?)",
                         (cursor.lastrowid, str(csv_path.resolve()), line, source_sha, int(is_synthetic)))
            if any(fila.get(k) not in (None, "") for k in ("precipitation_mm", "temp_c", "humidity")):
                climate = [float(fila[k]) if fila.get(k) not in (None, "") else None
                           for k in ("precipitation_mm", "temp_c", "humidity")]
                if any(v is not None and not math.isfinite(v) for v in climate):
                    raise ValueError(f"Clima no finito: {csv_path}:{line}")
                conn.execute("INSERT INTO eventos_enriquecidos VALUES(?,?,?,?,?)",
                             (cursor.lastrowid, *climate, es_dia_festivo_mx(fila['date'])))
            insertados += 1

        conn.commit()
        print(f"  {video_source:20s}  +{insertados:4d} registros  ({omitidos} ya existían)")
        total_insertados += insertados
        total_omitidos   += omitidos

    total_en_bd = conn.execute("SELECT COUNT(*) FROM eventos_espera").fetchone()[0]
    conn.close()

    print(f"\n{'='*60}")
    print(f"  Insertados ahora : {total_insertados}")
    print(f"  Ya existían      : {total_omitidos}")
    print(f"  Total en BD      : {total_en_bd}")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# COMANDO: enriquecer
# ─────────────────────────────────────────────────────────────────────────────

def obtener_clima_openmeteo(fecha_str: str) -> dict[int, dict] | None:
    """
    Consulta Open-Meteo Historical API para obtener el clima de la estación
    durante TODAS las horas de una fecha dada.

    Sin registro, sin API key. Documentación: https://open-meteo.com/en/docs/historical-weather-api

    Retorna un dict keyed por hora (0-23):
        { 0: {"precipitation_mm": 0.0, "temp_c": 14.5, "humidity": 78}, ... }
    o None si falla la petición.
    """
    if not HAS_REQUESTS:
        print("  [!] Instala 'requests': pip install requests")
        return None

    try:
        url = (
            "https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={ESTACION_LAT}&longitude={ESTACION_LON}"
            f"&start_date={fecha_str}&end_date={fecha_str}"
            "&hourly=temperature_2m,relative_humidity_2m,precipitation"
            "&timezone=America%2FMexico_City"
        )
        cache = (Path(WEATHER_CACHE) / f"{ESTACION_LAT:.4f}_{ESTACION_LON:.4f}_{fecha_str}.json"
                 if WEATHER_CACHE else None)
        import json
        if cache and cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))["response"]
        else:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                with cache.open("x", encoding="utf-8") as stream:
                    json.dump({"url": url, "response": data}, stream, allow_nan=False)

        hourly       = data["hourly"]
        tiempos      = hourly["time"]          # ["2025-06-02T00:00", ...]
        temperaturas = hourly["temperature_2m"]
        humedades    = hourly["relative_humidity_2m"]
        precipit     = hourly["precipitation"]

        resultado: dict[int, dict] = {}
        for i, t in enumerate(tiempos):
            hora = int(t.split("T")[1].split(":")[0])
            resultado[hora] = {
                "precipitation_mm": round(float(precipit[i]), 2) if precipit[i] is not None else None,
                "temp_c":           round(float(temperaturas[i]), 1) if temperaturas[i] is not None else None,
                "humidity":         int(humedades[i]) if humedades[i] is not None else None,
            }
        return resultado

    except Exception as e:
        print(f"  [!] Error Open-Meteo para {fecha_str}: {e}")
        return None


def es_dia_festivo_mx(fecha_str: str) -> int | None:
    """
    Devuelve 1 si la fecha es día festivo oficial en México, 0 si no.

    Festivos oficiales (Ley Federal del Trabajo, Art. 74):
        1 ene        Año Nuevo
        1er lun feb  Día de la Constitución
        3er lun mar  Natalicio de Benito Juárez
        1 may        Día del Trabajo
        16 sep       Independencia
        3er lun nov  Día de la Revolución
        25 dic       Navidad

    Requiere: pip install holidays
    """
    if not HAS_HOLIDAYS:
        print("  [!] Instala 'holidays': pip install holidays")
        return None

    fecha = date.fromisoformat(fecha_str)
    festivos_mx = holidays.Mexico(years=fecha.year)
    return 1 if fecha in festivos_mx else 0


def cmd_enriquecer() -> None:
    """
    Para cada fila en eventos_espera que no tenga par en eventos_enriquecidos,
    obtiene variables contextuales externas y las inserta en esa tabla:

      · Clima (Open-Meteo Historical API, gratis, sin API key):
            precipitation_mm  — precipitación en mm (continuo, no binario)
            temp_c            — temperatura a 2 m en °C
            humidity          — humedad relativa 0-100 %
        Coordenadas: estación Río Consulado–Misterios (lat=19.4624, lon=-99.1297).
        Se hace UNA petición HTTP por fecha única (devuelve las 24 horas) y el
        resultado se guarda en cache_fecha para no repetir la consulta en la
        misma ejecución.

      · Días festivos (librería 'holidays', sin conexión):
            is_holiday — 0 = día normal, 1 = festivo oficial México (Art. 74 LFT)

    El proceso es incremental: solo se procesan IDs sin par en eventos_enriquecidos,
    por lo que re-ejecutar el comando nunca duplica ni sobreescribe datos ya guardados.
    """
    conn = conectar_db()
    inicializar_db(conn)

    pendientes = conn.execute(
        """
        SELECT e.id,
               CASE WHEN e.track_id_persona = -1 THEN substr(e.bus_arrival, 1, 10) ELSE e.date END AS date,
               CASE WHEN e.track_id_persona = -1 THEN CAST(substr(e.bus_arrival, 12, 2) AS INTEGER) ELSE e.hour END AS hour
        FROM eventos_espera e
        LEFT JOIN eventos_enriquecidos ee ON e.id = ee.id
        WHERE ee.id IS NULL OR ee.precipitation_mm IS NULL OR ee.temp_c IS NULL
              OR ee.humidity IS NULL OR ee.is_holiday IS NULL
        ORDER BY e.date, e.hour
        """
    ).fetchall()

    if not pendientes:
        print("[✓] Todos los registros ya están enriquecidos.")
        conn.close()
        return

    print(f"\n  Registros a enriquecer : {len(pendientes)}")

    # Cache: fecha → {hora: {precipitation_mm, temp_c, humidity}}
    # Una petición HTTP por fecha única (no por registro)
    cache_fecha: dict[str, dict[int, dict] | None] = {}

    procesados  = 0
    sin_clima   = 0

    for row in pendientes:
        fila_id = row["id"]
        fecha   = row["date"]
        hora    = row["hour"]

        # ── Clima (Open-Meteo) ─────────────────────────────────────────────
        if fecha not in cache_fecha:
            print(f"  → Descargando clima para {fecha} (Open-Meteo)...")
            cache_fecha[fecha] = obtener_clima_openmeteo(fecha)

        clima_dia = cache_fecha[fecha]
        clima     = clima_dia.get(hora) if clima_dia else None
        if clima is None:
            sin_clima += 1

        # ── Día festivo (holidays, sin conexión) ───────────────────────────
        is_holiday = es_dia_festivo_mx(fecha)

        conn.execute(
            """
            INSERT INTO eventos_enriquecidos
                (id, precipitation_mm, temp_c, humidity, is_holiday)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                precipitation_mm = COALESCE(eventos_enriquecidos.precipitation_mm, excluded.precipitation_mm),
                temp_c = COALESCE(eventos_enriquecidos.temp_c, excluded.temp_c),
                humidity = COALESCE(eventos_enriquecidos.humidity, excluded.humidity),
                is_holiday = COALESCE(eventos_enriquecidos.is_holiday, excluded.is_holiday)
            """,
            (
                fila_id,
                clima["precipitation_mm"] if clima else None,
                clima["temp_c"]           if clima else None,
                clima["humidity"]         if clima else None,
                is_holiday,
            ),
        )
        procesados += 1

    conn.commit()
    conn.close()

    fechas_unicas = len(cache_fecha)
    print(f"\n{'='*60}")
    print(f"  Registros enriquecidos : {procesados}")
    print(f"  Peticiones HTTP        : {fechas_unicas}  (1 por fecha única)")
    if sin_clima:
        print(f"  Sin datos de clima     : {sin_clima}  (revisar fechas en Open-Meteo)")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# COMANDO: exportar
# ─────────────────────────────────────────────────────────────────────────────

def cmd_exportar() -> None:
    """
    Une eventos_espera + eventos_enriquecidos mediante LEFT JOIN y exporta
    el resultado como dataset_final.csv, listo para usar con pandas / sklearn / XGBoost.

    El LEFT JOIN garantiza que los registros sin datos de clima (is_holiday=None,
    precipitation_mm=None, etc.) también se incluyan en la exportación, permitiendo
    detectar qué franjas aún necesitan enriquecimiento.

    Columnas del CSV final (18 columnas; incluye centinelas y procedencia):
        waiting_time_min  — variable objetivo del modelo predictivo (en minutos)
        waiting_time_seg  — mismo valor en segundos
        arrival_user      — timestamp de llegada de la persona (YYYY-MM-DD HH:MM:SS)
        bus_arrival       — timestamp del bus asociado          (YYYY-MM-DD HH:MM:SS)
        date              — fecha del evento                    (YYYY-MM-DD)
        hour              — hora del evento                     (0-23)
        day               — día de la semana en inglés          (Monday … Sunday)
        is_weekend        — 0 = entre semana, 1 = fin de semana
        is_holiday        — 0 = día normal,   1 = festivo oficial México
        precipitation_mm  — precipitación en mm                 (REAL, continuo)
        temp_c            — temperatura a 2 m en °C             (REAL)
        humidity          — humedad relativa                     (INTEGER, 0-100)
    """
    # No crear accidentalmente una BD vacía al exportar desde una ruta errónea.
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"No existe la base seleccionada: {DB_PATH}")
    conn = sqlite3.connect(f"{DB_PATH.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    has_provenance = conn.execute("SELECT 1 FROM sqlite_master WHERE name='eventos_procedencia' AND type='table'").fetchone()
    provenance_columns = ("p.is_synthetic, p.source_file, p.source_line, p.source_sha256"
                          if has_provenance else "NULL AS is_synthetic, NULL AS source_file, NULL AS source_line, NULL AS source_sha256")
    provenance_join = "LEFT JOIN eventos_procedencia p ON e.id=p.id" if has_provenance else ""

    filas = conn.execute(
        f"""
        SELECT
            e.waiting_time_min,
            e.waiting_time_seg,
            e.arrival_user,
            e.bus_arrival,
            e.date,
            e.hour,
            e.day,
            e.is_weekend,
            ee.is_holiday,
            ee.precipitation_mm,
            ee.temp_c,
            ee.humidity,
            e.track_id_persona,
            e.video_source,
            CASE WHEN e.track_id_persona = -1 THEN 'bus_only' ELSE 'user_wait' END AS record_type,
            CASE WHEN e.track_id_persona >= 0 THEN 1 ELSE 0 END AS wait_observed,
            {provenance_columns}
        FROM eventos_espera e
        LEFT JOIN eventos_enriquecidos ee ON e.id = ee.id
        {provenance_join}
        ORDER BY e.arrival_user
        """
    ).fetchall()
    conn.close()

    if not filas:
        print("[!] No hay datos en la BD. Ejecuta primero: python consolidar_dataset.py consolidar")
        return

    COLS_EXPORT = [
        "waiting_time_min", "waiting_time_seg",
        "arrival_user", "bus_arrival", "date", "hour", "day", "is_weekend",
        "is_holiday", "precipitation_mm", "temp_c", "humidity",
        "track_id_persona", "video_source", "record_type", "wait_observed",
        "station_id", "camera_id",
        "is_synthetic", "source", "source_file", "source_line", "source_sha256",
    ]

    CSV_FINAL.parent.mkdir(parents=True, exist_ok=True)
    with open(CSV_FINAL, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLS_EXPORT)
        for row in filas:
            values = dict(row)
            synthetic = values['is_synthetic']
            values.update(station_id=STATION_ID, camera_id=None if synthetic == 1 else CAMERA_ID,
                          source='synthetic' if synthetic == 1 else 'camera' if synthetic == 0 else 'unknown')
            writer.writerow([values[c] for c in COLS_EXPORT])

    total       = len(filas)
    con_clima   = sum(1 for r in filas if r["precipitation_mm"] is not None)
    con_festivo = sum(1 for r in filas if r["is_holiday"] is not None)

    print(f"\n{'='*60}")
    print(f"  Dataset exportado : {CSV_FINAL}")
    print(f"  Total registros   : {total}")
    print(f"  Filas centinela    : {sum(r['record_type'] == 'bus_only' for r in filas)} (conservadas)")
    print(f"  Con clima         : {con_clima}  ({100*con_clima//total}%)")
    print(f"  Con is_holiday    : {con_festivo}  ({100*con_festivo//total}%)")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    global DB_PATH, CSV_FINAL, RESULTADOS, STATION_ID, CAMERA_ID, SELECTED_DATES, WEATHER_CACHE, INCLUDE_GENERATED, SYNTHETIC_DATES
    p = argparse.ArgumentParser(
        description="TT 2026-B050 | Consolidación y enriquecimiento del dataset"
    )
    sub = p.add_subparsers(dest="comando", required=True)
    p.add_argument("--db", type=Path, default=DB_PATH, help="Base de datos explícita")
    p.add_argument("--csv-final", type=Path, default=CSV_FINAL, help="Destino del dataset completo")
    p.add_argument("--resultados", type=Path, default=RESULTADOS, help="Directorio de CSVs por video")
    p.add_argument("--station-id", default=STATION_ID)
    p.add_argument("--camera-id", default=CAMERA_ID)
    p.add_argument("--dates", action="append", help="Fecha ISO autorizada; repetir --dates para añadir fechas")
    p.add_argument("--weather-cache", type=Path, help="Respuestas históricas para reproducir el enriquecimiento")
    p.add_argument("--include-generated", action="store_true", help="Admitir también CSV *_dataset.csv; exige declarar sus fechas sintéticas")
    p.add_argument("--synthetic-dates", action="append", default=[], help="Fecha ISO sintética; repetir por cada jornada generada")

    sub.add_parser(
        "consolidar",
        help="Lee todos los CSVs v4 e inserta registros nuevos en dataset_maestro.db (idempotente)",

    )

    sub.add_parser(
        "enriquecer",
        help="Añade clima (Open-Meteo, gratis) y días festivos a los registros en la BD",
    )

    sub.add_parser(
        "exportar",
        help="Exporta el dataset consolidado + enriquecido como CSV final",
    )

    args = p.parse_args()
    DB_PATH, CSV_FINAL, RESULTADOS = args.db, args.csv_final, args.resultados
    STATION_ID, CAMERA_ID = args.station_id, args.camera_id
    SELECTED_DATES, WEATHER_CACHE = args.dates, args.weather_cache
    INCLUDE_GENERATED, SYNTHETIC_DATES = args.include_generated, set(args.synthetic_dates)

    if args.comando == "consolidar":
        cmd_consolidar()
    elif args.comando == "enriquecer":
        cmd_enriquecer()
    elif args.comando == "exportar":
        cmd_exportar()


if __name__ == "__main__":
    main()
