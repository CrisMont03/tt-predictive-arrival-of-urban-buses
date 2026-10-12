"""Lectura anticipada acotada e inferencia por lotes en orden cronológico.

El lector es el único propietario de VideoCapture. YOLO y el tracking permanecen
en el hilo principal; no se comparten modelos entre hilos ni se dividen los IDs.
"""

from queue import Empty, Full, Queue
from threading import Event, Thread
from time import perf_counter


class FrameReader:
    def __init__(self, path, interval=3, prefetch=2, capture_factory=None):
        if interval < 1 or prefetch < 0:
            raise ValueError("interval debe ser >= 1 y prefetch >= 0")
        self.path = path
        self.interval = interval
        self.prefetch = prefetch
        self.capture_factory = capture_factory
        self.frames_read = 0
        self.read_seconds = 0.0
        self.wait_seconds = 0.0
        self.last_sample_id = None
        self._stop = Event()
        self._queue = Queue(maxsize=prefetch) if prefetch else None
        self._thread = None
        self._capture = None

    def _open(self):
        factory = self.capture_factory
        if factory is None:
            import cv2
            factory = cv2.VideoCapture
        cap = factory(self.path)
        if not cap.isOpened():
            cap.release()
            raise OSError(f"No se pudo abrir el video: {self.path}")
        return cap

    def __enter__(self):
        if self.prefetch:
            self._thread = Thread(target=self._produce, name="lector-video", daemon=True)
            self._thread.start()
        else:
            self._capture = self._open()
        return self

    def _read(self, cap):
        while not self._stop.is_set():
            inicio = perf_counter()
            ret, frame = cap.read()
            self.read_seconds += perf_counter() - inicio
            if not ret:
                return
            frame_id = self.frames_read
            self.frames_read += 1
            if frame_id % self.interval == 0:
                yield frame_id, frame

    def _put(self, item):
        while not self._stop.is_set():
            try:
                self._queue.put(item, timeout=0.1)
                return
            except Full:
                continue

    def _produce(self):
        cap = None
        error = None
        try:
            cap = self._open()
            for item in self._read(cap):
                self._put(("frame", item))
        except Exception as exc:
            error = exc
        finally:
            if cap is not None:
                cap.release()
            self._put(("fin", error))

    def __iter__(self):
        if not self.prefetch:
            for frame_id, frame in self._read(self._capture):
                self.last_sample_id = frame_id
                yield frame_id, frame
            return
        while not self._stop.is_set():
            inicio = perf_counter()
            try:
                kind, value = self._queue.get(timeout=0.1)
            except Empty:
                if not self._thread.is_alive() and self._queue.empty():
                    raise RuntimeError("El lector terminó sin indicar el fin del video")
                continue
            finally:
                self.wait_seconds += perf_counter() - inicio
            if kind == "fin":
                if value is not None:
                    raise RuntimeError(f"Error leyendo el video: {self.path}") from value
                return
            self.last_sample_id = value[0]
            yield value

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            # Una lectura nativa bloqueada no debe bloquear indefinidamente Ctrl+C.
            self._thread.join(timeout=5.0)
        if self._capture is not None:
            self._capture.release()


def ordered_inference(reader, model, batch_size=1, stats=None, **kwargs):
    """Infiere lotes pequeños y entrega cada resultado en el orden de lectura."""
    if batch_size < 1:
        raise ValueError("batch_size debe ser >= 1")
    if stats is None:
        stats = {}
    stats.setdefault("inference_seconds", 0.0)
    stats.setdefault("inference_calls", 0)
    stats.setdefault("frames_inferred", 0)
    source = iter(reader)
    while True:
        items = []
        for _ in range(batch_size):
            try:
                items.append(next(source))
            except StopIteration:
                break
        if not items:
            return
        # Un lote de tamaño 1 conserva la entrada ndarray del pipeline original.
        frames = [frame for _, frame in items]
        inicio = perf_counter()
        results = model(frames[0] if len(frames) == 1 else frames, **kwargs)
        stats["inference_seconds"] += perf_counter() - inicio
        stats["inference_calls"] += 1
        if len(results) != len(items):
            raise RuntimeError("YOLO devolvió una cantidad de resultados distinta al lote")
        stats["frames_inferred"] += len(items)
        for (frame_id, frame), result in zip(items, results):
            yield frame_id, frame, result
