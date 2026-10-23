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

## Sección E — Expo Go, modelo y mecanismos A/B sin push

La preparación operativa se documenta en [Infra/README.md](Infra/README.md).
Incluye `/ready`, descarga de paquetes fijados por generación/hash, imágenes de API
y entrenamiento, infraestructura en modo inspección y ciclo mensual con snapshots,
benchmark independiente, pesos cámara/feedback, aprobación y rollback separados.
`Modelo/retrain_job.py` ejecuta por defecto pruebas sintéticas locales; nunca publica
sus resultados. Cloud Scheduler y promoción automática no están activados.

FCM, APNs, Apple Developer y builds nativos quedan fuera del alcance actual.
Se conservan Auth/Firestore del SDK JS y alertas dentro de Expo Go. El Mecanismo A
actúa por consulta; B es mensual. No existe todavía LSTM aprobado ni despliegue GCP.

No confundir la preparación con producción: faltan Docker activo, validación del
entorno ML Linux, primeras métricas reales, autorización de recursos y pruebas
físicas. El promotor rechaza modelos sintéticos y no usa la prueba final de tesis.

No se modificó el detector YOLO ni su calibración. La base SQLite original no se modificó durante las verificaciones.

## Inicio rápido

App dentro de Expo Go (entorno de desarrollo, no build independiente):

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

## Sección D: recorrido completo sin LSTM

La caché es exclusivamente del servidor: la app siempre llama a `/predict`.
Un hit evita otra inferencia, pero no la autenticación, preparación y registro de
la consulta. No se abrieron reglas de Firestore para acceso móvil a `predictions`
ni para escrituras móviles en historial o feedback.

El runner conserva `MODEL_NOT_READY` por defecto. Para pruebas manuales, permite
`--predictor integration --context fixture` solo con confirmación de `arribo-68326`;
ese predictor no es un LSTM y sus resultados, caché y feedback conservan
`simulation=true`. `/health` distingue `predictor_kind`, `predictor_ready` y
`model_ready`; un predictor de integración no indica modelo real disponible.

Guía de arranque, contrato y prueba manual:
[Firebase/pruebas-integracion-d.md](Firebase/pruebas-integracion-d.md).
No se cargaron fixtures en Firebase real ni se ejecutó el modelo en esta entrega.

## Datos completos y preparación

Desde `Codigo`, para no sobrescribir el CSV anterior durante pruebas:

```sh
python3 Dataset/consolidar_dataset.py --csv-final Dataset/output/dataset_final.csv exportar
python3 Dataset/preparar_modelo.py --csv Dataset/output/dataset_final.csv --output Dataset/output/modelo --window 20
python3 -m unittest discover -s Dataset/tests -v
```

Las rutas predeterminadas leen los CSV fuente de `Procesamiento_de_videos/output/dataset` y escriben la consolidación en `Dataset/output`. Se pueden seleccionar con `--db`, `--csv-final` y `--resultados` **antes** del subcomando. Para consolidar/enriquecer instala `requests` y `holidays` (también disponibles en el entorno del backend). Revisa los destinos antes de ejecutar la exportación, que reemplaza su CSV de salida. Guía completa: [Sección B](Dataset/README.md).

El CSV tiene 18 columnas. Conserva todas las filas: `bus_only`, `track_id_persona=-1`, `waiting_time_min=0`, `wait_observed=0`; usuarios: `user_wait`, `wait_observed=1`, incluso si su espera real es cero. La máscara no elimina centinelas: distingue ausencia de pasajero de espera observada. Los centinelas aportan arribos y contexto al entrenamiento, pero su cero no se utiliza como etiqueta de espera de un usuario.

`preparar_modelo.py` genera `sequences.jsonl` con **todos** los registros, incluyendo centinelas con peso de pérdida cero, y `quality.json`. Un bus con varios pasajeros cuenta una vez. Las esperas históricas solo son disponibles después del arribo del bus asociado; no se usa la propia etiqueta futura. Sin manifiesto verificado, cada video/fecha queda aislado para evitar inferir intervalos a través de huecos. Copia y completa `manifest.example.csv`; el ejemplo tiene `verified=0` y no establece continuidad real. Opcional: `--manifest ruta.csv`.

El antiguo ejemplo de 773 registros fue una prueba, no el dataset real en construcción. La sección B se verifica con muestras sintéticas en directorios temporales, sin procesar ni sobrescribir los CSV reales. `can_train=false` impide entrenar sobre una partición inválida. Tres jornadas habilitan una partición básica; no bastan por sí solas para aprobar generalización. `meets_collection_goal` distingue además la meta de 5,000 esperas utilizables, sin contar centinelas.

## Entrenamiento futuro

Cuando la recopilación y validación permitan train/validation/test por jornadas:

```sh
python3 -m venv Modelo/.venv
Modelo/.venv/bin/python -m pip install -r Modelo/requirements.txt
Modelo/.venv/bin/python Modelo/entrenar.py --data Dataset/output/modelo --output Modelo/output/candidato-v1 --version candidato-v1
```

El directorio de salida debe ser nuevo. El contrato `arribo-sequences-v2` concatena 27 valores por paso: siete variables contextuales y máscaras, espera y disponibilidad, cuatro variables de buses y máscaras, y tres indicadores de origen de espera. El LSTM recibe además una máscara explícita de pasos. Los respaldos de entrenamiento usan etiquetas completadas antes de cada consulta; para validación, prueba y producción se congelan los de entrenamiento. El escalador solo se ajusta con `train`. Las centinelas permanecen en la preparación con peso cero para la pérdida. Se comparan escenarios con llegadas históricas, operativo sin llegadas y sin feedback. XGBoost recibe la ventana completa y la máscara, igual información que el LSTM. El modelo requiere revisión independiente y no se despliega automáticamente.

La rutina de entrenamiento y la carga real TensorFlow están preparadas, pero **no se han ejecutado ni validado con un modelo real** en esta entrega. Instala y prueba sus dependencias en el entorno ML elegido antes de la primera corrida. No existe un artefacto entrenado ni se han medido MAE/RMSE/R² reales.

El artefacto queda `approved=false`. Tras revisar métricas, franjas, cobertura/diversidad, fuentes de inferencia y una validación operativa independiente, el responsable debe aprobarlo explícitamente. Producción exige `eligible=true`, `approved=true`, contrato y checksum correctos; instala `Backend/requirements-model.txt` y configura `ARRIBO_MODEL_DIR`. La cámara solo genera el dataset: no se conectará al backend. No se infieren arribos exactos de reportes; esa rama permanece enmascarada en producción. El Mecanismo A incorpora las esperas recientes de usuarios ya recibidas, no simuladas y dentro del dominio y horizonte de dos horas. El paquete incluye `model.keras` y `metadata.json` con escaladores, respaldos y contrato; no admite un modelo antiguo de 24 entradas como si fuera compatible.

## Firebase y despliegue pendientes

Se necesita elegir/configurar proyecto Firebase, habilitar Email/Password, Firestore, credenciales ADC del backend e IAM de mínimo privilegio. Ejecutar pruebas de reglas con Emulator Suite antes de publicar. La app no tiene permisos de escritura sobre caché, historial, feedback validado ni alertas; esas escrituras son del servidor.

Después de revisar cuentas y seguridad: verificar reglas/índices/TTL y activar la infraestructura Cloud Run/Storage/Secret Manager preparada en E. EAS y builds nativos quedan fuera del alcance. El Dockerfile permite `--build-arg WITH_MODEL=true` para instalar TensorFlow; el paquete inmutable se carga desde directorio local o GCS por generación/hash. No se construyó ni desplegó la imagen. Nunca cargar claves administrativas en variables `EXPO_PUBLIC_*`.

`Backend/admin_alertas.py` publica/retira avisos con ADC/IAM. Es una acción administrativa manual; no se ejecutó durante esta entrega. La lectura climática y TomTom tiene timeout y conserva ausencias como `null`. **No hay monitor automático de contingencia ambiental:** `env_alert_active=null` hasta seleccionar una fuente oficial fiable y verificar su vigencia. Tampoco se habilitaron push, Scheduler, reentrenamiento mensual/promoción automática o medición de latencia de producción. Estas etapas necesitan infraestructura, datos y evaluación; la configuración preparada no equivale a despliegue.

## Verificaciones y seguridad

Backend: `.venv/bin/python -m pytest -q` desde `Backend`. Dataset: `unittest` arriba. App: instrucciones en su README. Se comprobaron tipos, lint, compatibilidad de dependencias y bundles Android/iOS/web. No se ejecutaron pruebas con cuentas Firebase reales ni en un dispositivo físico.

La auditoría npm reportó 34 avisos transitivos (23 altos, 11 moderados). Varios pertenecen a la cadena Expo/Metro; el arreglo automático propuesto incluye downgrades incompatibles (Expo 44, Firebase 9) y cambios mayores. No se aplicó `audit fix --force`. Estos avisos requieren una revisión/actualización compatible antes de publicar. La comprobación `expo install --check` confirma compatibilidad, **no** ausencia de vulnerabilidades. [Documentación de npm audit](https://docs.npmjs.com/cli/v11/commands/npm-audit).
