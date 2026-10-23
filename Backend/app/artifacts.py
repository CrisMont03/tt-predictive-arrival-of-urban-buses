"""Immutable model packages. GCS generations and a trusted manifest hash are mandatory."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from .model_inputs import SCHEMA_VERSION

FILES = {"metadata.json": 4_000_000, "model.keras": 250_000_000, "xgboost.json": 100_000_000}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, default=str).encode()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def immutable_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json_bytes(value)
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError(f"No se puede reemplazar el snapshot: {path.name}")
    return digest(data)


def gcs_parts(uri):
    parsed = urlparse(uri)
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.strip("/") or parsed.query or parsed.fragment:
        raise ValueError("Se requiere una URI gs://bucket/objeto")
    parts = parsed.path.lstrip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Ruta de objeto inválida")
    return parsed.netloc, "/".join(parts)


def validate_manifest(manifest):
    if (manifest.get("format") != "arribo-package-v1" or manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("simulation") is not False
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", manifest.get("release_id", ""))):
        raise ValueError("Paquete incompatible o simulado")
    entries = manifest.get("files", {})
    if not {"model.keras", "metadata.json"} <= entries.keys() or not entries.keys() <= FILES.keys():
        raise ValueError("Archivos de paquete inválidos")
    for name, entry in entries.items():
        if (not isinstance(entry.get("generation"), int) or entry["generation"] < 1
                or not re.fullmatch(r"[0-9a-f]{64}", entry.get("sha256", ""))
                or not isinstance(entry.get("size"), int) or not 0 < entry["size"] <= FILES[name]):
            raise ValueError("Generación, tamaño o hash inválido")


def download_package(uri, generation, sha256, *, client=None, directory=None):
    if int(generation) < 1 or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Faltan generación y hash confiables del manifiesto")
    bucket_name, object_name = gcs_parts(uri)
    if client is None:
        from google.cloud import storage
        client = storage.Client()
    bucket = client.bucket(bucket_name)

    def fetch(name, gen, limit):
        blob = bucket.blob(name, generation=gen)
        blob.reload(if_generation_match=gen)
        if blob.size is None or not 0 < blob.size <= limit:
            raise ValueError("Objeto vacío o demasiado grande")
        return blob.download_as_bytes(if_generation_match=gen, checksum="auto")

    data = fetch(object_name, int(generation), 32_000)
    if digest(data) != sha256:
        raise ValueError("Hash del manifiesto incorrecto")
    manifest = json.loads(data)
    validate_manifest(manifest)
    target = Path(directory) if directory else Path(tempfile.mkdtemp(prefix="arribo-model-"))
    target.mkdir(parents=True, exist_ok=True)
    prefix = object_name.rsplit("/", 1)[0] + "/" if "/" in object_name else ""
    for name, entry in manifest["files"].items():
        content = fetch(prefix + name, entry["generation"], FILES[name])
        if len(content) != entry["size"] or digest(content) != entry["sha256"]:
            raise ValueError("Integridad del paquete incorrecta")
        with (target / name).open("xb") as stream:
            stream.write(content)
    return target, manifest


def publish_package(directory, prefix_uri, release_id, *, client=None):
    """Administrative operation; never called by the API or retrainer."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", release_id):
        raise ValueError("Identificador de publicación inválido")
    metadata = read_json(Path(directory) / "metadata.json")
    if metadata.get("simulation") or metadata.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("No publicar simulaciones ni contratos anteriores")
    if digest((Path(directory) / "model.keras").read_bytes()) != metadata["model_sha256"]:
        raise ValueError("Checksum del modelo incorrecto")
    bucket_name, prefix = gcs_parts(prefix_uri)
    if client is None:
        from google.cloud import storage
        client = storage.Client()
    bucket = client.bucket(bucket_name)
    files = {}

    def upload(blob, content, **kwargs):
        from google.api_core.exceptions import PreconditionFailed
        try:
            blob.upload_from_string(content, if_generation_match=0, **kwargs)
        except PreconditionFailed:
            # An interrupted publication may be retried, but never overwritten.
            blob.reload()
            if blob.download_as_bytes(if_generation_match=blob.generation) != content:
                raise ValueError("La publicación inmutable ya contiene otros bytes")

    for name, limit in FILES.items():
        path = Path(directory) / name
        if not path.exists():
            continue
        content = path.read_bytes()
        if not 0 < len(content) <= limit:
            raise ValueError("Tamaño de artefacto inválido")
        blob = bucket.blob(f"{prefix}/{release_id}/{name}")
        upload(blob, content)
        files[name] = dict(generation=int(blob.generation), sha256=digest(content), size=len(content))
    manifest = dict(format="arribo-package-v1", schema_version=SCHEMA_VERSION,
                    release_id=release_id, simulation=False, files=files)
    validate_manifest(manifest)
    content = json_bytes(manifest)
    blob = bucket.blob(f"{prefix}/{release_id}/manifest.json")
    upload(blob, content, content_type="application/json")
    return dict(uri=f"gs://{bucket_name}/{blob.name}", generation=int(blob.generation), sha256=digest(content), release_id=release_id)


def configured_model_directory():
    local, uri = os.getenv("ARRIBO_MODEL_DIR"), os.getenv("ARRIBO_MODEL_MANIFEST_URI")
    if local and uri:
        raise ValueError("Configura modelo local o remoto, no ambos")
    if not uri:
        return local, None
    return download_package(uri, os.environ["ARRIBO_MODEL_MANIFEST_GENERATION"],
                            os.environ["ARRIBO_MODEL_MANIFEST_SHA256"])
