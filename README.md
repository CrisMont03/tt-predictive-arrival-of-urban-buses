# ARRIBO · TT 2026-B050

Implementación local del plan, con separación explícita entre demostración y producción. El predictor todavía no está entrenado.

## Componentes

| Carpeta | Implementado |
|---|---|
| `App_Movil` | Acceso, cuatro pestañas, feedback, demo, persistencia local, Auth/Firestore y cola offline. |
| `Dataset` | Consolidación idempotente por origen/evento, enriquecimiento reintentable, exportación completa y secuencias causales. |
| `Modelo` | Entrenador de candidatos: promedio histórico, XGBoost y LSTM; no despliega automáticamente. |
| `Backend` | FastAPI, tokens, snapshots, caché, idempotencia, feedback, contexto externo y carga opcional de artefacto aprobado. |
| `Firebase` | Reglas de aislamiento, índices y política TTL preparada; no desplegados. |
| `docs` | Plan completo y estado verificable. |

No se modificó el detector YOLO ni su calibración. La base SQLite original no se modificó durante las verificaciones.

## Inicio rápido

App independiente en Expo Go:

```sh
cd App_Movil
npm ci
npx expo start --go
```

Backend de prueba, desde `Codigo/Backend`:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
ARRIBO_MODE=demo .venv/bin/python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8080
```

OpenAPI: `http://localhost:8080/docs`. `/predict` y `/feedback` en demo aceptan `Authorization: Bearer demo-alice`. En producción **solo** aceptan tokens Firebase válidos. Sin artefacto, `/health` informa `model_ready=false` y `/predict` devuelve 503 `MODEL_NOT_READY`.

`timestamp` indica cuándo se generó la estimación; `requested_at`, cuándo se hizo la consulta. Un hit de caché conserva el primero y registra un segundo nuevo. El feedback utiliza la fecha de consulta del servidor, no la hora de creación del caché ni un timestamp suministrado por el cliente.

## Datos completos y preparación

Desde `Codigo`, para no sobrescribir el CSV anterior durante pruebas:

```sh
python3 Dataset/consolidar_dataset.py --csv-final Dataset/output/dataset_final.csv exportar
python3 Dataset/preparar_modelo.py --csv Dataset/output/dataset_final.csv --output Dataset/output/modelo --window 20
python3 -m unittest discover -s Dataset/tests -v
```

Las rutas predeterminadas apuntan a `../../Dataset/Dataset-final`; se pueden seleccionar con `--db`, `--csv-final` y `--resultados` **antes** del subcomando. Para consolidar/enriquecer instala `requests` y `holidays` (también disponibles en el entorno del backend). Conserva una copia antes de escribir sobre la base/dataset real.

El CSV tiene 18 columnas. Conserva todas las filas: `bus_only`, `track_id_persona=-1`, `waiting_time_min=0`, `wait_observed=0`; usuarios: `user_wait`, `wait_observed=1`, incluso si su espera real es cero. La máscara no elimina centinelas: distingue ausencia de pasajero de espera observada. Los centinelas aportan arribos y contexto al entrenamiento, pero su cero no se utiliza como etiqueta de espera de un usuario.

`preparar_modelo.py` genera `sequences.jsonl` con **todos** los registros, incluyendo centinelas con peso de pérdida cero, y `quality.json`. Un bus con varios pasajeros cuenta una vez. Las esperas históricas solo son disponibles después del arribo del bus asociado; no se usa la propia etiqueta futura. Sin manifiesto verificado, cada video/fecha queda aislado para evitar inferir intervalos a través de huecos. Copia y completa `manifest.example.csv`; el ejemplo tiene `verified=0` y no establece continuidad real. Opcional: `--manifest ruta.csv`.

Resultado observado: 773 registros (772 usuarios, 1 centinela), 50 arribos únicos, una sola fecha. Todos tienen clima y festivo enriquecidos. `can_train=false` impide entrenar sobre una partición inválida. Tres jornadas habilitan una partición básica; no bastan por sí solas para aprobar generalización.

## Entrenamiento futuro

Cuando la recopilación y validación permitan train/validation/test por jornadas:

```sh
python3 -m venv Modelo/.venv
Modelo/.venv/bin/python -m pip install -r Modelo/requirements.txt
Modelo/.venv/bin/python Modelo/entrenar.py --data Dataset/output/modelo --output Modelo/output/candidato-v1 --version candidato-v1
```

El directorio de salida debe ser nuevo. Los preprocesadores se ajustan solo con entrenamiento. Siete variables temporales/climáticas, historial de espera con máscara y cuatro variables de buses se concatenan en 24 entradas por paso. Padding izquierdo con máscara; LSTM sin cuDNN para admitirlo. El entrenamiento conserva centinelas con peso cero para la pérdida y simula falta de contexto. Evalúa baselines y LSTM, guarda calibración de intervalo, hashes, ventana, esquema y métricas, incluida prueba sin historial.

La rutina de entrenamiento y la carga real TensorFlow están preparadas, pero **no se han ejecutado ni validado con un modelo real** en esta entrega. Instala y prueba sus dependencias en el entorno ML elegido antes de la primera corrida. No existe un artefacto entrenado ni se han medido MAE/RMSE/R² reales.

El artefacto queda `approved=false`. Tras revisar métricas, franjas, cobertura/diversidad, fuentes de inferencia y una validación operativa independiente, el responsable debe aprobarlo explícitamente. Producción exige `eligible=true`, `approved=true` y checksum correcto; instala `Backend/requirements-model.txt` y configura `ARRIBO_MODEL_DIR`. No se infieren arribos de buses a partir de reportes de usuarios: sin cámara online, esa rama permanece enmascarada. El feedback reciente se usa solo si ya fue recibido, no es simulado, tiene contexto y está dentro del dominio inicial y de las últimas dos horas.

## Firebase y despliegue pendientes

Se necesita elegir/configurar proyecto Firebase, habilitar Email/Password, Firestore, credenciales ADC del backend e IAM de mínimo privilegio. Ejecutar pruebas de reglas con Emulator Suite antes de publicar. La app no tiene permisos de escritura sobre caché, historial, feedback validado ni alertas; esas escrituras son del servidor.

Después de configurar cuentas y revisar seguridad: publicar reglas/índices/TTL, preparar Cloud Run/Storage/Secret Manager y EAS. El Dockerfile permite `--build-arg WITH_MODEL=true` para instalar TensorFlow; montar/descargar el artefacto inmutable fuera de la imagen y establecer `ARRIBO_MODEL_DIR`. No se construyó ni desplegó la imagen. Nunca cargar claves administrativas en variables `EXPO_PUBLIC_*`.

`Backend/admin_alertas.py` publica/retira avisos con ADC/IAM. Es una acción administrativa manual; no se ejecutó durante esta entrega. La lectura climática y TomTom tiene timeout y conserva ausencias como `null`. **No hay monitor automático de contingencia ambiental:** `env_alert_active=null` hasta seleccionar una fuente oficial fiable y verificar su vigencia. Tampoco se habilitaron push, Scheduler, reentrenamiento mensual/promoción automática o medición de latencia de producción. Estas etapas necesitan infraestructura, datos y evaluación; la configuración preparada no equivale a despliegue.

## Verificaciones y seguridad

Backend: `.venv/bin/python -m pytest -q` desde `Backend`. Dataset: `unittest` arriba. App: instrucciones en su README. Se comprobaron tipos, lint, compatibilidad de dependencias y bundles Android/iOS/web. No se ejecutaron pruebas con cuentas Firebase reales ni en un dispositivo físico.

La auditoría npm reportó 34 avisos transitivos (23 altos, 11 moderados). Varios pertenecen a la cadena Expo/Metro; el arreglo automático propuesto incluye downgrades incompatibles (Expo 44, Firebase 9) y cambios mayores. No se aplicó `audit fix --force`. Estos avisos requieren una revisión/actualización compatible antes de publicar. La comprobación `expo install --check` confirma compatibilidad, **no** ausencia de vulnerabilidades. [Documentación de npm audit](https://docs.npmjs.com/cli/v11/commands/npm-audit).
