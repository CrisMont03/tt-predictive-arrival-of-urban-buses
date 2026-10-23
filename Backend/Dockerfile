FROM python:3.11.14-slim
WORKDIR /service
COPY requirements*.txt .
ARG WITH_MODEL=false
RUN if [ "$WITH_MODEL" = "true" ]; then pip install --no-cache-dir -r requirements-model.txt; else pip install --no-cache-dir -r requirements.txt; fi
COPY app ./app
RUN useradd --uid 10001 --create-home arribo
USER 10001
ENV ARRIBO_MODE=production PORT=8080 PYTHONUNBUFFERED=1
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8080}"]
