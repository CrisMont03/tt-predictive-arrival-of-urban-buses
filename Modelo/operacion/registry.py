"""CAS registry and expiring leases. GCS uses generation preconditions; local uses flock."""
import contextlib
import json
import threading
import time
import uuid
from pathlib import Path

from app.artifacts import gcs_parts, json_bytes


class ConcurrentRun(RuntimeError):
    pass


class LocalRegistry:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        if any(part in {"", ".", ".."} for part in key.split("/")) or key.startswith("/"):
            raise ValueError("Clave inválida")
        return self.directory / (key + ".json")

    @contextlib.contextmanager
    def _lock(self):
        import fcntl
        with (self.directory / ".lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def read(self, key):
        with self._lock():
            path = self._path(key)
            if not path.exists():
                return None, 0
            envelope = json.loads(path.read_text())
            return envelope["value"], envelope["generation"]

    def cas(self, key, value, generation):
        with self._lock():
            path = self._path(key)
            old = json.loads(path.read_text()) if path.exists() else {"generation": 0}
            if old["generation"] != generation:
                raise ConcurrentRun("El registro cambió")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp-" + uuid.uuid4().hex)
            temporary.write_bytes(json_bytes(dict(value=value, generation=generation + 1)))
            temporary.replace(path)
            return generation + 1


class GCSRegistry:
    def __init__(self, uri, client=None):
        if client is None:
            from google.cloud import storage
            client = storage.Client()
        name, self.prefix = gcs_parts(uri)
        self.bucket = client.bucket(name)

    def read(self, key):
        from google.api_core.exceptions import NotFound
        blob = self.bucket.blob(f"{self.prefix}/{key}.json")
        try:
            blob.reload()
            data = blob.download_as_bytes(if_generation_match=blob.generation)
            return json.loads(data), int(blob.generation)
        except NotFound:
            return None, 0

    def cas(self, key, value, generation):
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(f"{self.prefix}/{key}.json")
        try:
            blob.upload_from_string(json_bytes(value), if_generation_match=generation, content_type="application/json")
        except PreconditionFailed as error:
            raise ConcurrentRun("El registro cambió") from error
        return int(blob.generation)


class Lease:
    def __init__(self, registry, owner, *, seconds=120, clock=time.time, key="leases/retraining"):
        self.registry, self.owner, self.seconds, self.clock, self.key = registry, owner, seconds, clock, key
        self.token, self.lost = uuid.uuid4().hex, False
        self.stop = threading.Event()
        self.thread = None

    def acquire(self):
        old, generation = self.registry.read(self.key)
        if old and old["expires"] > self.clock():
            raise ConcurrentRun("Ya existe una ejecución activa")
        self.registry.cas(self.key, dict(owner=self.owner, token=self.token, expires=self.clock() + self.seconds), generation)
        return self

    def check(self):
        value, _ = self.registry.read(self.key)
        if self.lost or not value or value["token"] != self.token or value["expires"] <= self.clock():
            raise ConcurrentRun("La ejecución perdió el bloqueo; se cancela")

    def renew(self):
        self.check()
        value, generation = self.registry.read(self.key)
        if value["token"] != self.token:
            raise ConcurrentRun("Bloqueo reemplazado")
        self.registry.cas(self.key, {**value, "expires": self.clock() + self.seconds}, generation)

    def __enter__(self):
        self.acquire()
        def heartbeat():
            while not self.stop.wait(self.seconds / 3):
                try:
                    self.renew()
                except Exception:
                    self.lost = True
                    return
        self.thread = threading.Thread(target=heartbeat, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=5)
        value, generation = self.registry.read(self.key)
        if value and value["token"] == self.token:
            self.registry.cas(self.key, {**value, "expires": 0}, generation)
