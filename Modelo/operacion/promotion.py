"""Separate deployment authority; validate before traffic, retain rollback evidence."""
import time

from app.artifacts import digest, json_bytes
from .registry import Lease


class CloudRunDriver:
    @staticmethod
    def targets(traffic, statuses):
        """Preserve tagged URLs so readiness and later rollback keep working."""
        values = [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": key, "percent": value}
                  for key, value in traffic.items()]
        for item in statuses:
            if not item.get("tag"):
                continue
            target = next((value for value in values if value["revision"] == item["revision"] and not value.get("tag")), None)
            if target is None:
                target = dict(type="TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", revision=item["revision"], percent=0)
                values.append(target)
            target["tag"] = item["tag"]
        return values

    def __init__(self, project, region, service, image):
        if "@sha256:" not in image:
            raise ValueError("Fija la imagen por digest, no por etiqueta")
        self.project, self.region, self.service, self.image = project, region, service, image
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.session = AuthorizedSession(credentials)
        self.name = f"projects/{project}/locations/{region}/services/{service}"
        self.url = "https://run.googleapis.com/v2/" + self.name

    def patch(self, body, mask):
        response = self.session.patch(self.url, params={"updateMask": mask}, json=body, timeout=30)
        response.raise_for_status()
        operation = response.json()
        for _ in range(180):
            if operation.get("done"):
                if operation.get("error"):
                    raise RuntimeError("Cloud Run rechazó la operación")
                return
            time.sleep(5)
            response = self.session.get("https://run.googleapis.com/v2/" + operation["name"], timeout=30)
            response.raise_for_status()
            operation = response.json()
        raise TimeoutError("Cloud Run no completó la operación")

    def describe(self):
        response = self.session.get(self.url, timeout=30)
        response.raise_for_status()
        return response.json()

    def traffic(self):
        values = self.describe().get("trafficStatuses", [])
        return {item["revision"]: item["percent"] for item in values if item.get("percent", 0)}

    def deploy(self, package, release_id):
        suffix = "r" + digest(json_bytes([package, release_id]))[:16]
        env = dict(ARRIBO_MODE="production", ARRIBO_MODEL_DIR="",
                   ARRIBO_MODEL_MANIFEST_URI=package["uri"],
                   ARRIBO_MODEL_MANIFEST_GENERATION=str(package["generation"]),
                   ARRIBO_MODEL_MANIFEST_SHA256=package["sha256"])
        existing = self.describe()
        template = existing["template"]
        template["revision"] = self.name + "/revisions/" + self.service + "-" + suffix
        container = template["containers"][0]
        container["image"] = self.image
        container["env"] = [entry for entry in container.get("env", []) if entry["name"] not in env]
        container["env"].extend({"name": key, "value": value} for key, value in env.items())
        traffic = self.targets(self.traffic(), existing.get("trafficStatuses", []))
        traffic.append(dict(type="TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", revision=template["revision"], percent=0, tag=suffix))
        self.patch(dict(name=self.name, etag=existing["etag"], template=template, traffic=traffic), "template,traffic")
        description = self.describe()
        tagged = next(item for item in description["trafficStatuses"] if item.get("tag") == suffix)
        return dict(revision=tagged["revision"], url=tagged["uri"])

    def ready(self, deployment, expected):
        import httpx
        for _ in range(12):
            try:
                response = httpx.get(deployment["url"] + "/ready", timeout=10)
                value = response.json()
                if (response.status_code == 200 and value.get("ready") is True
                        and value.get("simulation") is False and value.get("release_id") == expected["release_id"]
                        and value.get("model_version") == expected["model_version"]):
                    return True
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(5)
        return False

    def switch(self, traffic):
        if not traffic or sum(traffic.values()) != 100:
            raise ValueError("Distribución de tráfico inválida")
        existing = self.describe()
        values = self.targets(traffic, existing.get("trafficStatuses", []))
        self.patch(dict(name=self.name, etag=existing["etag"], traffic=values), "traffic")


def promote(registry, driver, package, evaluation, *, expected_incumbent, model_version,
            manual_initial=False, enabled=False):
    if not enabled:
        return {"status": "disabled"}
    if evaluation.get("simulation") is not False:
        raise ValueError("Una simulación nunca autoriza publicar")
    if not evaluation.get("publishable") and not (manual_initial and evaluation.get("decision") == "manual_initial_approval"):
        raise ValueError("No existe mejora aprobable")
    if evaluation.get("candidate_version") != model_version or evaluation.get("incumbent_version") != expected_incumbent:
        raise ValueError("La comparación no corresponde a estas versiones")
    with Lease(registry, package["release_id"], key="leases/promotion") as lease:
        current, gen = registry.read("deployment/current")
        key = "deployments/" + package["release_id"]
        prior, prior_gen = registry.read(key)
        if prior and prior.get("status") == "active":
            return prior
        if prior and prior.get("status") == "switching":
            # Recover an unknown outcome after process termination before trying any new release.
            if prior["previous_traffic"]:
                driver.switch(prior["previous_traffic"])
            if current and current.get("package", {}).get("release_id") == package["release_id"]:
                registry.cas("deployment/current", prior["previous"], gen)
            prior["status"] = "recovered_after_interruption"
            registry.cas(key, prior, prior_gen)
            old, suspend_gen = registry.read("promotion/suspended")
            registry.cas("promotion/suspended", {"suspended": True, "reason": "interrupted_promotion"}, suspend_gen)
            return prior
        if current and current.get("model_version") != expected_incumbent:
            raise ValueError("El vigente cambió desde la evaluación")
        if current is None and expected_incumbent is not None:
            raise ValueError("Primero registra/aprueba la publicación inicial")
        previous_traffic = driver.traffic()
        record = dict(status="deploying", previous_traffic=previous_traffic, previous=current,
                      package=package, model_version=model_version,
                      evaluation_sha256=digest(json_bytes(evaluation)))
        prior_gen = registry.cas(key, record, prior_gen)
        switched = False
        try:
            deployment = driver.deploy(package, package["release_id"])
            record["deployment"] = deployment
            if not driver.ready(deployment, {"release_id": package["release_id"], "model_version": model_version}):
                raise ValueError("La nueva revisión no está lista")
            lease.check()
            if driver.traffic() != previous_traffic:
                raise ValueError("El tráfico cambió por otra operación")
            # Mark intention before the external mutation: rollback is safe if the RPC outcome is unknown.
            record["status"] = "switching"
            prior_gen = registry.cas(key, record, prior_gen)
            switched = True
            driver.switch({deployment["revision"]: 100})
            if not driver.ready(deployment, {"release_id": package["release_id"], "model_version": model_version}):
                raise ValueError("Fallo después de transferir tráfico")
            lease.check()
            registry.cas("deployment/current", dict(model_version=model_version, package=package,
                         deployment=deployment, previous=current, previous_traffic=previous_traffic), gen)
            record["status"] = "active"
            registry.cas(key, record, prior_gen)
            return record
        except Exception:
            if switched and previous_traffic:
                driver.switch(previous_traffic)
                active, active_gen = registry.read("deployment/current")
                if active and active.get("package", {}).get("release_id") == package["release_id"]:
                    registry.cas("deployment/current", current, active_gen)
                suspended, suspend_gen = registry.read("promotion/suspended")
                registry.cas("promotion/suspended", {"suspended": True, "reason": "failed_promotion"}, suspend_gen)
            record["status"] = "rolled_back" if switched and previous_traffic else "failed_before_traffic"
            registry.cas(key, record, prior_gen)
            raise


def rollback(registry, driver, *, enabled=False):
    if not enabled:
        return {"status": "disabled"}
    with Lease(registry, "rollback", key="leases/promotion"):
        current, gen = registry.read("deployment/current")
        if not current or not current.get("previous") or not current.get("previous_traffic"):
            raise ValueError("No hay una versión anterior registrada")
        previous = current["previous"]
        if not driver.ready(previous["deployment"], {"release_id": previous["package"]["release_id"],
                                                     "model_version": previous["model_version"]}):
            raise ValueError("La revisión anterior no está lista")
        driver.switch(current["previous_traffic"])
        registry.cas("deployment/current", previous, gen)
        value, lock_gen = registry.read("promotion/suspended")
        registry.cas("promotion/suspended", {"suspended": True, "reason": "manual_rollback"}, lock_gen)
        return {"status": "rolled_back", "model_version": previous["model_version"]}
