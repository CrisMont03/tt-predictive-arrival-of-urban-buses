FROM python:3.11.14-slim
WORKDIR /workspace
COPY Backend/requirements*.txt ./Backend/
COPY Modelo/requirements.txt ./Modelo/requirements.txt
RUN pip install --no-cache-dir -r Modelo/requirements.txt
COPY Backend/app ./Backend/app
COPY Dataset/preparar_modelo.py ./Dataset/preparar_modelo.py
COPY Modelo/entrenar.py Modelo/retrain_job.py Modelo/promote_job.py ./Modelo/
COPY Modelo/operacion ./Modelo/operacion
RUN useradd --uid 10001 --create-home arribo && mkdir -p /workspace/Modelo/output && chown 10001:10001 /workspace/Modelo/output
USER 10001
ENV PYTHONUNBUFFERED=1 GOOGLE_CLOUD_PROJECT=arribo-68326
ENTRYPOINT ["python", "Modelo/retrain_job.py"]
