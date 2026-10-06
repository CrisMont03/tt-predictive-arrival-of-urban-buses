import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import consolidar_dataset as consolidate
from preparar_modelo import build, read_rows, read_manifest


def row(track, arrival, bus, minutes=0, video="one"):
    return {"track_id_persona": str(track), "arrival_user": f"2025-06-02 {arrival}", "bus_arrival": f"2025-06-02 {bus}", "waiting_time_min": str(minutes), "waiting_time_seg": str(minutes * 60), "date": "2025-06-02", "hour": "6", "day": "Monday", "is_weekend": "0", "is_holiday": "0", "precipitation_mm": "", "temp_c": "19", "humidity": "65", "video_source": video, "record_type": "bus_only" if track == -1 else "user_wait", "wait_observed": "0" if track == -1 else "1", "station_id": "rio_consulado", "camera_id": "camera"}


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def write(self, rows):
        path = self.root / "data.csv"
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
        return path

    def test_export_keeps_sentinel_and_real_zero(self):
        db = self.root / "data.db"
        conn = sqlite3.connect(db)
        consolidate.inicializar_db(conn)
        for item in [row(-1, "06:00:00", "06:00:00"), row(1, "06:00:00", "06:00:00")]:
            conn.execute("INSERT INTO eventos_espera(video_source,track_id_persona,arrival_user,bus_arrival,waiting_time_min,waiting_time_seg,date,hour,day,is_weekend) VALUES(?,?,?,?,?,?,?,?,?,?)", tuple(item[key] for key in ["video_source", *consolidate.COLS_CSV]))
        conn.commit(); conn.close()
        output = self.root / "final.csv"
        with patch.object(consolidate, "DB_PATH", db), patch.object(consolidate, "CSV_FINAL", output):
            consolidate.cmd_exportar()
        rows = read_rows(output)
        self.assertEqual(len(rows), 2)
        self.assertEqual([int(item["wait_observed"]) for item in rows], [0, 1])

    def test_bus_only_contributes_and_passengers_do_not_duplicate_bus(self):
        rows = read_rows(self.write([row(-1, "06:00:00", "06:00:00"), row(1, "06:03:00", "06:10:00", 7), row(2, "06:04:00", "06:10:00", 6), row(3, "06:12:00", "06:20:00", 8)]))
        examples, report = build(rows, 10)
        self.assertEqual(report["unique_buses"], 3)
        self.assertEqual(examples[0]["loss_weight"], 0)
        self.assertEqual(examples[1]["inputs"]["bus_context"][-1][0], 3)
        self.assertEqual(examples[1]["inputs"]["past_wait_mask"][-1], [0])
        self.assertEqual(examples[-1]["inputs"]["past_wait"][-1], [6.5])
        self.assertEqual(examples[-1]["inputs"]["bus_context"][-1][:2], [2, 10])
        self.assertFalse(report["can_train"])

    def test_context_does_not_cross_unverified_video_or_use_future_labels(self):
        rows = read_rows(self.write([row(-1, "06:00:00", "06:00:00"), row(1, "06:05:00", "06:10:00", 5, "two")]))
        examples, _ = build(rows, 20)
        self.assertEqual(examples[-1]["inputs"]["bus_mask"][-1], [0, 0, 0, 0])
        self.assertEqual(examples[-1]["inputs"]["past_wait_mask"][-1], [0])

    def test_enrichment_retries_missing_columns_and_preserves_known_weather(self):
        db = self.root / "data.db"
        conn = sqlite3.connect(db); consolidate.inicializar_db(conn)
        item = row(-1, "06:00:00", "06:00:00")
        conn.execute("INSERT INTO eventos_espera(video_source,track_id_persona,arrival_user,bus_arrival,waiting_time_min,waiting_time_seg,date,hour,day,is_weekend) VALUES(?,?,?,?,?,?,?,?,?,?)", tuple(item[key] for key in ["video_source", *consolidate.COLS_CSV]))
        conn.execute("INSERT INTO eventos_enriquecidos VALUES(1,NULL,19,65,NULL)"); conn.commit(); conn.close()
        with patch.object(consolidate, "DB_PATH", db), patch.object(consolidate, "obtener_clima_openmeteo", return_value={6: {"precipitation_mm": 0, "temp_c": 99, "humidity": 80}}), patch.object(consolidate, "es_dia_festivo_mx", return_value=0):
            consolidate.cmd_enriquecer()
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute("SELECT precipitation_mm,temp_c,humidity,is_holiday FROM eventos_enriquecidos").fetchone(), (0,19,65,0))

    def test_real_wait_timestamp_validation(self):
        with self.assertRaises(ValueError):
            read_rows(self.write([row(1, "06:00:00", "06:10:00", 2)]))

    def test_verified_manifest_rejects_gaps(self):
        path = self.root / "manifest.csv"
        path.write_text("video_source,date,station_id,camera_id,start_at,end_at,continuity_group,verified\none,2025-06-02,rio_consulado,camera,2025-06-02 06:00:00,2025-06-02 07:00:00,g,1\ntwo,2025-06-02,rio_consulado,camera,2025-06-02 07:10:00,2025-06-02 08:00:00,g,1\n")
        with self.assertRaises(ValueError):
            read_manifest(path)


if __name__ == "__main__":
    unittest.main()
