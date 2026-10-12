"""Pruebas de orden, cierre y propagación de fallos sin cargar YOLO."""

import sys
import argparse
import ast
import contextlib
import io
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_pipeline import FrameReader, ordered_inference


class FakeCapture:
    def __init__(self, count=10, fail_at=None, opened=True):
        self.count = count
        self.fail_at = fail_at
        self.opened = opened
        self.index = 0
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        if self.index == self.fail_at:
            raise OSError("error de lectura")
        if self.index >= self.count:
            return False, None
        value = self.index
        self.index += 1
        return True, value

    def release(self):
        self.released = True


class PipelineTests(unittest.TestCase):
    def test_same_frames_and_ids_in_both_modes(self):
        for count in [0, 1, 2, 3, 4, 10, 11]:
            for prefetch in [0, 1, 2]:
                with self.subTest(count=count, prefetch=prefetch):
                    cap = FakeCapture(count)
                    with FrameReader("fake", prefetch=prefetch, capture_factory=lambda _: cap) as reader:
                        items = list(reader)
                    self.assertEqual(items, [(i, i) for i in range(0, count, 3)])
                    self.assertEqual(reader.frames_read, count)
                    self.assertTrue(cap.released)

    def test_batches_preserve_order_and_partial_last_batch(self):
        for prefetch in [0, 2]:
            for batch_size in [1, 2, 3]:
                calls = []
                def model(source, **kwargs):
                    calls.append(source)
                    return [f"result-{frame}" for frame in (source if isinstance(source, list) else [source])]
                cap = FakeCapture(13)
                stats = {}
                with FrameReader("fake", prefetch=prefetch, capture_factory=lambda _: cap) as reader:
                    result = list(ordered_inference(reader, model, batch_size, stats, conf=0.25))
                self.assertEqual(result, [(i, i, f"result-{i}") for i in [0, 3, 6, 9, 12]])
                self.assertEqual(stats["frames_inferred"], 5)
                self.assertEqual(stats["inference_calls"], (5 + batch_size - 1) // batch_size)
                if batch_size == 1:
                    self.assertTrue(all(not isinstance(c, list) for c in calls))

    def test_reader_error_is_propagated_and_capture_released(self):
        for prefetch in [0, 2]:
            cap = FakeCapture(10, fail_at=4)
            with self.assertRaises((OSError, RuntimeError)):
                with FrameReader("fake", prefetch=prefetch, capture_factory=lambda _: cap) as reader:
                    list(reader)
            self.assertTrue(cap.released)
            if prefetch:
                self.assertFalse(reader._thread.is_alive())

    def test_unopenable_video(self):
        for prefetch in [0, 2]:
            cap = FakeCapture(opened=False)
            with self.assertRaises((OSError, RuntimeError)):
                with FrameReader("fake", prefetch=prefetch, capture_factory=lambda _: cap) as reader:
                    list(reader)
            self.assertTrue(cap.released)

    def test_early_exit_does_not_leave_producer_blocked_on_full_queue(self):
        cap = FakeCapture(10000)
        with FrameReader("fake", prefetch=1, capture_factory=lambda _: cap) as reader:
            self.assertEqual(next(iter(reader)), (0, 0))
        self.assertFalse(reader._thread.is_alive())
        self.assertTrue(cap.released)

    def test_model_failure_closes_reader(self):
        cap = FakeCapture(10000)
        def model(source):
            raise RuntimeError("error de inferencia")
        with self.assertRaisesRegex(RuntimeError, "error de inferencia"):
            with FrameReader("fake", prefetch=1, capture_factory=lambda _: cap) as reader:
                list(ordered_inference(reader, model))
        self.assertTrue(cap.released)
        self.assertFalse(reader._thread.is_alive())

    def test_result_count_mismatch_is_not_silently_dropped(self):
        with self.assertRaisesRegex(RuntimeError, "cantidad de resultados"):
            list(ordered_inference([(0, "a"), (3, "b")], lambda _: ["one"], batch_size=2))

    def test_invalid_parameters(self):
        for interval, prefetch in [(0, 2), (3, -1)]:
            with self.assertRaises(ValueError):
                FrameReader("fake", interval=interval, prefetch=prefetch)
        with self.assertRaises(ValueError):
            list(ordered_inference([], lambda _: [], batch_size=0))

    def test_cli_and_video_batch_forward_parallel_options(self):
        path = Path(__file__).resolve().parents[1] / "procesar_video_v4.py"
        tree = ast.parse(path.read_text())
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {"main", "procesar_lote"}]
        calls = []
        namespace = {"argparse": argparse, "Path": Path, "procesar_video": lambda **kw: calls.append(kw)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
        previous_argv = sys.argv
        try:
            sys.argv = ["v4", "one.mp4", "--timestamp-inicio", "2025-10-03 05:00:00"]
            namespace["main"]()
            self.assertEqual(calls[-1]["prefetch_frames"], 2)
            self.assertEqual(calls[-1]["batch_size"], 1)
            sys.argv = [
                "v4", "--videos", "one.mp4", "two.mp4", "--timestamps-inicio",
                "2025-10-03 05:00:00", "2025-10-03 06:00:00",
                "--prefetch-frames", "0", "--batch-size", "2",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                namespace["main"]()
            self.assertTrue(all(c["prefetch_frames"] == 0 and c["batch_size"] == 2 for c in calls[-2:]))
            self.assertEqual(calls[-1]["cola_anterior_path"], "output/one_v4_cola.json")
        finally:
            sys.argv = previous_argv


if __name__ == "__main__":
    unittest.main()
