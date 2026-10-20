import csv
from copy import deepcopy
from contextlib import closing
import json
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import consolidar_dataset as consolidate
from preparar_modelo import build, read_rows, read_manifest
from app.model_inputs import fit_scaler, online_inputs, timestamp, transform


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
        self.assertEqual(examples[-1]["inputs"]["past_wait"][-3:-1], [[7], [6]])
        self.assertEqual(examples[-1]["inputs"]["past_wait_mask"][-1], [0])
        self.assertEqual(examples[-1]["inputs"]["bus_context"][-1][:2], [2, 10])
        self.assertEqual(examples[-1]["operational_inputs"]["bus_mask"], [[0] * 4] * 10)
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
        with closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute("SELECT precipitation_mm,temp_c,humidity,is_holiday FROM eventos_enriquecidos").fetchone(), (0,19,65,0))

    def test_real_wait_timestamp_validation(self):
        with self.assertRaises(ValueError):
            read_rows(self.write([row(1, "06:00:00", "06:10:00", 2)]))

    def test_verified_manifest_rejects_gaps(self):
        path = self.root / "manifest.csv"
        path.write_text("video_source,date,station_id,camera_id,start_at,end_at,continuity_group,verified\none,2025-06-02,rio_consulado,camera,2025-06-02 06:00:00,2025-06-02 07:00:00,g,1\ntwo,2025-06-02,rio_consulado,camera,2025-06-02 07:10:00,2025-06-02 08:00:00,g,1\n")
        with self.assertRaises(ValueError):
            read_manifest(path)

    def multi_day(self):
        items = []
        for date in ("2025-06-02", "2025-06-03", "2025-06-04"):
            for item in [row(-1, "06:00:00", "06:00:00"), row(1, "06:01:00", "06:10:00", 9), row(2, "06:15:00", "06:25:00", 10)]:
                item = {key: value.replace("2025-06-02", date) for key, value in item.items()}
                items.append(item)
        return items

    def test_holdout_cannot_change_scaler_or_frozen_fallback(self):
        raw = self.multi_day()
        examples, quality = build(read_rows(self.write(raw)))
        self.assertTrue(quality["can_train"])
        scaler = fit_scaler([item for item in examples if item["split"] == "train"])
        for item in raw:
            if item["date"] != "2025-06-02":
                item["temp_c"] = "99"
                if item["track_id_persona"] == "2":
                    item.update(waiting_time_min="20", waiting_time_seg="1200", bus_arrival=f"{item['date']} 06:35:00")
        changed, report = build(read_rows(self.write(raw)))
        self.assertEqual(quality["fallback"], report["fallback"])
        self.assertEqual(scaler, fit_scaler([item for item in changed if item["split"] == "train"]))
        self.assertEqual([item for item in examples if item["split"] == "train"], [item for item in changed if item["split"] == "train"])

    def test_training_fallback_cannot_include_current_or_future_target(self):
        original = self.multi_day()
        before, _ = build(read_rows(self.write(original)))
        changed = deepcopy(original)
        changed[2].update(waiting_time_min="20", waiting_time_seg="1200", bus_arrival="2025-06-02 06:35:00")
        after, _ = build(read_rows(self.write(changed)))
        old = next(item for item in before if item["event_at"] == "2025-06-02T12:15:00+00:00")
        new = next(item for item in after if item["event_at"] == old["event_at"])
        for branch in ("inputs", "operational_inputs", "cold_inputs"):
            self.assertEqual(old[branch], new[branch])
        self.assertEqual(old["cold_inputs"]["past_wait"][-2], [9])
        first = next(item for item in before if item["event_at"] == "2025-06-02T12:01:00+00:00")
        self.assertEqual(first["cold_inputs"]["past_wait_mask"], [[0]] * 10)

    def test_offline_operational_input_matches_server_recipe_exactly(self):
        examples, quality = build(read_rows(self.write(self.multi_day())))
        # Select validation: completed earlier validation report may be used;
        # fallback is exclusively frozen training data.
        current = next(item for item in examples if item["event_at"] == "2025-06-03T12:15:00+00:00")
        original = next(item for item in examples if item["event_at"] == "2025-06-03T12:01:00+00:00")
        names = ["hour", "day_of_week", "is_weekend", "is_holiday", "precipitation_mm", "temp_c", "humidity"]
        def context(item):
            return {key: value if mask else None for key, value, mask in zip(names, item["operational_inputs"]["temporal"][-1], item["operational_inputs"]["temporal_mask"][-1])}
        report = dict(query_id=original["sample_id"], query_at=original["event_at"],
                      received_at=original["bus_arrival"], actual_min=original["target"],
                      context=context(original), station_id="rio_consulado", simulation=False)
        prepared = online_inputs(context(current), 10, now=current["event_at"],
                                 observations=[report], fallback=quality["fallback"],
                                 start_at="2025-06-03T12:00:00Z")
        self.assertEqual(prepared, current["operational_inputs"])
        scaler = fit_scaler([item for item in examples if item["split"] == "train"])
        self.assertEqual(transform(prepared, scaler), transform(current["operational_inputs"], scaler))

    def test_partition_start_has_no_prior_partition_observation(self):
        examples, quality = build(read_rows(self.write(self.multi_day())))
        for item in examples:
            if item["split"] in ("validation", "test") and item["event_at"].endswith("T12:01:00+00:00"):
                self.assertNotIn([1, 0, 0], item["inputs"]["wait_origin"])
                self.assertIn([0, 1, 0], item["inputs"]["wait_origin"])

    def test_manifest_rejects_completion_outside_verified_coverage(self):
        rows = read_rows(self.write([row(1, "06:05:00", "06:10:00", 5)]))
        manifest = {("rio_consulado", "camera", "one", "2025-06-02"):
                    (("group",), timestamp("2025-06-02 06:00:00"), timestamp("2025-06-02 06:06:00"))}
        with self.assertRaises(ValueError, msg="finalización"):
            build(rows, manifest=manifest)

    def test_verified_continuity_allows_previous_video_observations(self):
        rows = read_rows(self.write([row(1, "06:01:00", "06:10:00", 9, "one"), row(2, "06:15:00", "06:25:00", 10, "two")]))
        group = ("rio_consulado", "camera", "2025-06-02", "verified-group")
        manifest = {
            ("rio_consulado", "camera", "one", "2025-06-02"): (group, timestamp("2025-06-02 06:00:00"), timestamp("2025-06-02 06:12:00")),
            ("rio_consulado", "camera", "two", "2025-06-02"): (group, timestamp("2025-06-02 06:12:00"), timestamp("2025-06-02 06:30:00")),
        }
        isolated, _ = build(rows)
        continuous, _ = build(rows, manifest=manifest)
        self.assertNotIn([1, 0, 0], isolated[-1]["inputs"]["wait_origin"])
        self.assertEqual(continuous[-1]["inputs"]["past_wait"][-2], [9])

    def test_real_zero_has_loss_but_sentinel_and_out_of_domain_do_not(self):
        rows = read_rows(self.write([row(-1, "06:00:00", "06:00:00"), row(1, "06:00:00", "06:00:00"), row(2, "06:01:00", "07:01:00", 60)]))
        examples, quality = build(rows)
        self.assertEqual(quality["rows"], 3)
        self.assertEqual(quality["eligible_waits"], 1)
        self.assertEqual(sum(item["loss_weight"] for item in examples), 1)
        self.assertEqual(next(item for item in examples if item["record_type"] == "bus_only")["loss_weight"], 0)

    def test_duplicate_input_rejected_instead_of_weighting_it_twice(self):
        with self.assertRaises(ValueError, msg="duplicado"):
            read_rows(self.write([row(1, "06:00:00", "06:10:00", 10)] * 2))

    def test_cli_creates_versioned_preparation_and_never_overwrites(self):
        path = self.write(self.multi_day())
        output = self.root / "prepared"
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / "preparar_modelo.py"),
                   "--csv", str(path), "--output", str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        quality = json.loads((output / "quality.json").read_text())
        self.assertEqual(quality["schema_version"], "arribo-sequences-v2")
        self.assertTrue(quality["can_train"])
        self.assertFalse(quality["meets_collection_goal"])
        import importlib.util
        spec = importlib.util.spec_from_file_location("trainer_cli_test", Path(__file__).resolve().parents[2] / "Modelo" / "entrenar.py")
        trainer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(trainer)
        loaded, partitions = trainer.load_preparation(output)
        self.assertEqual(loaded["fallback"], quality["fallback"])
        self.assertEqual(set(partitions), {"train", "validation", "test"})
        original = (output / "sequences.jsonl").read_bytes()
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(original, (output / "sequences.jsonl").read_bytes())
        (output / "sequences.jsonl").write_bytes(original + b"\n")
        with self.assertRaises(ValueError, msg="informe"):
            trainer.load_preparation(output)


if __name__ == "__main__":
    unittest.main()
