ARG API_BASE=python:3.11.14-slim
FROM ${API_BASE}
USER root
WORKDIR /workspace
COPY Backend/requirements*.txt ./Backend/
COPY Modelo/requirements.txt ./Modelo/requirements.txt
RUN pip install --no-cache-dir -r Backend/requirements-model.txt
COPY Backend/app ./Backend/app
COPY Dataset/preparar_modelo.py Dataset/next_bus.py ./Dataset/
COPY Modelo/entrenar.py Modelo/retrain_job.py Modelo/promote_job.py Modelo/review_model_package.py Modelo/next_bus.py ./Modelo/
COPY Modelo/operacion ./Modelo/operacion
RUN if ! id -u arribo >/dev/null 2>&1; then useradd --uid 10001 --create-home arribo; fi && mkdir -p /workspace/Modelo/output && chown 10001:10001 /workspace/Modelo/output
USER 10001
ENV PYTHONUNBUFFERED=1 GOOGLE_CLOUD_PROJECT=arribo-68326 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 TF_NUM_INTEROP_THREADS=2 TF_NUM_INTRAOP_THREADS=2
ENTRYPOINT ["python", "Modelo/retrain_job.py"]
