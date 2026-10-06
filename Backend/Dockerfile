FROM python:3.11-slim
WORKDIR /service
COPY requirements*.txt .
ARG WITH_MODEL=false
RUN if [ "$WITH_MODEL" = "true" ]; then pip install --no-cache-dir -r requirements-model.txt; else pip install --no-cache-dir -r requirements.txt; fi
COPY app ./app
ENV ARRIBO_MODE=production
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
