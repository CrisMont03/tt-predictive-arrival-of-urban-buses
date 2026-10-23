"""Print a deployment plan by default. Cloud mutations require --execute and confirmation."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess


def plan(config, stage):
    project, region = config["project"], config["region"]
    if project != "arribo-68326":
        raise ValueError("Solo está permitido el proyecto de desarrollo arribo-68326")
    service = config["service"]
    accounts = {name: f"arribo-{name}@{project}.iam.gserviceaccount.com" for name in ("api", "trainer", "scheduler", "promotor")}
    steps = []

    def command(args, exists=None):
        steps.append(dict(command=["gcloud", *args, "--project", project, "--quiet"],
                          exists=["gcloud", *exists, "--project", project] if exists else None))

    def project_role(identity, role):
        command(["projects", "add-iam-policy-binding", project, "--member", "serviceAccount:" + identity, "--role", role])

    def bucket_role(bucket, identity, role):
        command(["storage", "buckets", "add-iam-policy-binding", "gs://" + bucket,
                 "--member", "serviceAccount:" + identity, "--role", role])

    if stage == "bootstrap":
        command(["services", "enable", "run.googleapis.com", "artifactregistry.googleapis.com", "storage.googleapis.com",
                 "secretmanager.googleapis.com", "cloudscheduler.googleapis.com", "iam.googleapis.com"])
        for name in accounts:
            command(["iam", "service-accounts", "create", "arribo-" + name],
                    ["iam", "service-accounts", "describe", accounts[name]])
        command(["artifacts", "repositories", "create", config["repository"], "--repository-format", "docker", "--location", region],
                ["artifacts", "repositories", "describe", config["repository"], "--location", region])
        for name in ("datasets", "models", "candidates", "ops"):
            bucket = project + "-" + name
            command(["storage", "buckets", "create", "gs://" + bucket, "--location", region,
                     "--uniform-bucket-level-access", "--public-access-prevention"], ["storage", "buckets", "describe", "gs://" + bucket])
            command(["storage", "buckets", "update", "gs://" + bucket, "--versioning"])
        command(["secrets", "create", "tomtom-api-key", "--replication-policy", "automatic"], ["secrets", "describe", "tomtom-api-key"])
        command(["secrets", "add-iam-policy-binding", "tomtom-api-key", "--member", "serviceAccount:" + accounts["api"],
                 "--role", "roles/secretmanager.secretAccessor"])
        project_role(accounts["api"], "roles/datastore.user")
        project_role(accounts["api"], "roles/firebaseauth.viewer")
        project_role(accounts["trainer"], "roles/datastore.viewer")
        for identity in ("api", "trainer", "promotor"):
            bucket_role(project + "-models", accounts[identity], "roles/storage.objectViewer")
        bucket_role(project + "-datasets", accounts["trainer"], "roles/storage.objectViewer")
        bucket_role(project + "-candidates", accounts["trainer"], "roles/storage.objectCreator")
        bucket_role(project + "-candidates", accounts["promotor"], "roles/storage.objectViewer")
        bucket_role(project + "-models", accounts["promotor"], "roles/storage.objectCreator")
        for identity in ("trainer", "promotor"):
            bucket_role(project + "-ops", accounts[identity], "roles/storage.objectUser")
        command(["iam", "service-accounts", "add-iam-policy-binding", accounts["api"],
                 "--member", "serviceAccount:" + accounts["promotor"], "--role", "roles/iam.serviceAccountUser"])
    elif stage == "service":
        env = {"ARRIBO_MODE": "production", "GOOGLE_CLOUD_PROJECT": project, "ARRIBO_CODE_REVISION": config["code_revision"]}
        if config.get("model"):
            env.update(ARRIBO_MODEL_MANIFEST_URI=config["model"]["uri"],
                       ARRIBO_MODEL_MANIFEST_GENERATION=str(config["model"]["generation"]),
                       ARRIBO_MODEL_MANIFEST_SHA256=config["model"]["sha256"])
        command(["run", "deploy", service, "--region", region, "--image", config["api_image"],
                 "--service-account", accounts["api"], "--allow-unauthenticated", "--memory", "2Gi", "--cpu", "1",
                 "--min-instances", "0", "--max-instances", str(config["max_instances"]), "--concurrency", "4", "--timeout", "60s",
                 "--startup-probe", "httpGet.path=/health,initialDelaySeconds=0,periodSeconds=10,timeoutSeconds=5,failureThreshold=24",
                 "--set-env-vars", ",".join(f"{k}={v}" for k, v in env.items()),
                 "--set-secrets", "TOMTOM_API_KEY=tomtom-api-key:" + config["secret_version"]])
        command(["run", "services", "add-iam-policy-binding", service, "--region", region,
                 "--member", "serviceAccount:" + accounts["promotor"], "--role", "roles/run.developer"])
    elif stage == "jobs":
        common = dict(GOOGLE_CLOUD_PROJECT=project, ARRIBO_REGION=region, ARRIBO_SERVICE=service,
                      ARRIBO_REGISTRY_URI=f"gs://{project}-ops/maintenance", ARRIBO_CODE_REVISION=config["code_revision"])
        spec = config["training_spec"]
        trainer = {**common, "ARRIBO_CANDIDATES_URI": f"gs://{project}-candidates/candidates",
                   "ARRIBO_TRAINING_SPEC_URI": spec["uri"], "ARRIBO_TRAINING_SPEC_GENERATION": str(spec["generation"]),
                   "ARRIBO_TRAINING_SPEC_SHA256": spec["sha256"],
                   "ARRIBO_PROMOTOR_JOB": f"projects/{project}/locations/{region}/jobs/arribo-promote" if config["auto_promotion"] else ""}
        promotor = {**common, "ARRIBO_API_IMAGE": config["api_image"],
                    "ARRIBO_RELEASES_URI": f"gs://{project}-models/releases",
                    "ARRIBO_AUTO_PROMOTION": str(config["auto_promotion"]).lower()}
        for job, identity, script, env in (("arribo-retrain", "trainer", "retrain_job.py", trainer),
                                           ("arribo-promote", "promotor", "promote_job.py", promotor)):
            args = (f"Modelo/{script},--mode,cloud,--project,{project},--confirm-dev-project,{project}" if identity == "trainer"
                    else f"Modelo/{script},--execute,--confirm-dev-project,{project}")
            command(["run", "jobs", "deploy", job, "--region", region, "--image", config["job_image"],
                     "--service-account", accounts[identity], "--tasks", "1", "--parallelism", "1", "--max-retries", "1",
                     "--task-timeout", config["job_timeout"], "--memory", "4Gi", "--cpu", "2",
                     "--command", "python", "--args", args, "--set-env-vars", ",".join(f"{k}={v}" for k, v in env.items())])
        for job, identity in (("arribo-retrain", "scheduler"), ("arribo-promote", "trainer")):
            command(["run", "jobs", "add-iam-policy-binding", job, "--region", region,
                     "--member", "serviceAccount:" + accounts[identity], "--role", "roles/run.invoker"])
    elif stage == "schedule":
        command(["scheduler", "jobs", "create", "http", "arribo-monthly", "--location", region,
                 "--schedule", "0 3 1 * *", "--time-zone", "America/Mexico_City", "--http-method", "POST",
                 "--uri", f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/arribo-retrain:run",
                 "--oauth-service-account-email", accounts["scheduler"], "--message-body", "{}", "--max-retry-attempts", "0"],
                ["scheduler", "jobs", "describe", "arribo-monthly", "--location", region])
    return steps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.example.json"))
    parser.add_argument("--stage", choices=["bootstrap", "service", "jobs", "schedule"], default="bootstrap")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-dev-project")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    steps = plan(config, args.stage)
    if args.execute:
        if args.confirm_dev_project != config["project"]:
            parser.error("La ejecución requiere confirmar el proyecto")
        if args.stage in {"service", "jobs"} and ("@sha256:" not in config["api_image"] or "@sha256:" not in config["job_image"]):
            parser.error("Fija ambas imágenes por digest después de construirlas")
        if args.stage == "jobs" and "REPLACE" in json.dumps(config):
            parser.error("Completa los parámetros del ejemplo antes de crear Jobs")
    for step in steps:
        print(shlex.join(step["command"]))
        if args.execute:
            if step["exists"] and subprocess.run(step["exists"], capture_output=True).returncode == 0:
                continue
            subprocess.run(step["command"], check=True)


if __name__ == "__main__":
    main()
