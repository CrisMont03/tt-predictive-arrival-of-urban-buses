# Sección E: infraestructura preparada, no desplegada

ARRIBO sigue en Expo Go, con Firebase JS Auth/Firestore. No se añade FCM, APNs,
Apple Developer, EAS ni publicación en tiendas. No hay un LSTM aprobado todavía.

## 1. Comprobación local sin nube ni credenciales

Desde `Codigo`:

```sh
Backend/.venv/bin/python Modelo/retrain_job.py --scenario better
Backend/.venv/bin/python Modelo/retrain_job.py --scenario equal
Backend/.venv/bin/python Modelo/retrain_job.py --scenario worse
Backend/.venv/bin/python Modelo/retrain_job.py --scenario insufficient
Backend/.venv/bin/python Modelo/promote_job.py
Backend/.venv/bin/python Infra/prepare.py --stage bootstrap
```

Los resultados se guardan en `Modelo/output/section-e`, ignorado por Git. Los tres
primeros escenarios producen `fixture_improves`/`fixture_keeps`, siempre con
`simulation=true` y `publishable=false`. No son modelos ni métricas reales.
`--scenario invalid` falla intencionadamente y registra `failed`. Repetir una
prueba completada reutiliza la corrida; una fallida crea otro intento sin cambiar
el snapshot. `promote_job.py` sin `--execute` no contacta GCP.

El proceso de prueba recorre registro, snapshot, preparación de ejemplo,
entrenador/emisor de resultados controlados y evaluación. No afirma haber entrenado
un LSTM. La prueba Keras sintética opcional de `test_operations.py` sí ejecuta una
época y verifica guardado/carga si se ha instalado el entorno ML.

```sh
cd Backend
.venv/bin/python -m pytest tests/test_operations.py -q
```

Para un entorno nuevo instalar primero `Backend/requirements-dev.txt` (incluye
pytest y python-dotenv); las imágenes runtime no necesitan esas herramientas.

## 2. Imágenes y dependencias

Construir una vez que Docker esté activo, desde `Codigo`:

```sh
docker build --platform linux/amd64 --build-arg WITH_MODEL=true -t arribo-api:e-local Backend
docker build --platform linux/amd64 -f Modelo/Dockerfile -t arribo-jobs:e-local .
```

Las imágenes ejecutan como UID 10001, no llevan modelos ni credenciales y la API
respeta `PORT`. `requirements-constraints.txt` fija el baseline transitivo observado
en el entorno actual. TensorFlow/NumPy/XGBoost tienen versiones directas fijadas;
su resolución Linux y dependencias ML transitivas requieren la construcción y
validación real antes de publicar. No se presenta un lock ML con hashes como si
ya hubiera sido resuelto en Linux. Renovar y auditar pins antes de producción.

Publicar posteriormente las imágenes en Artifact Registry y registrar sus digests
`@sha256:...` en una copia local de `config.example.json`; no desplegar etiquetas
mutables. Registrar también el commit del código. Excluir del archivo cualquier
secreto: las claves viven en Secret Manager y ADC, nunca en `EXPO_PUBLIC_*`.

## 3. Modelo y configuración remota

Paquete por versión: `model.keras`, `metadata.json` y opcional `xgboost.json`.
`publish_package()` en `Backend/app/artifacts.py` sube objetos con precondición
`if_generation_match=0`. Solo tolera reintentos con bytes idénticos. El manifiesto
incluye generación, tamaño y SHA-256 de cada archivo. No admite rutas arbitrarias,
simulaciones ni esquemas anteriores. El descriptor contiene URI, generación y
hash del propio manifiesto; se fija en la revisión de Cloud Run.

Variables de la API (ver `Backend/.env.example`):

- `ARRIBO_MODE=production`, `GOOGLE_CLOUD_PROJECT=arribo-68326`.
- `ARRIBO_MODEL_MANIFEST_URI`, `ARRIBO_MODEL_MANIFEST_GENERATION`,
  `ARRIBO_MODEL_MANIFEST_SHA256`, o alternativamente `ARRIBO_MODEL_DIR` local.
- `TOMTOM_API_KEY` mediante una versión fija del secreto.
- `ARRIBO_CORS_ORIGINS` si se añade acceso web; no es una barrera de autenticación.

`/health` verifica vida del proceso; `/ready` devuelve 503 sin LSTM real y 200
con versión/publicación cargada. Un paquete configurado inválido aborta el arranque.
No se sustituye por el predictor simulado. La identidad de publicación invalida
la caché sin borrar las consultas ni su feedback.

## 4. Permisos y publicación futura — requieren autorización

`prepare.py` solo imprime comandos por defecto. Revisar todos antes de ejecutarlos.
No ejecuta builds ni publica reglas/índices. `--execute --confirm-dev-project
arribo-68326` autoriza la etapa solicitada, no todas las demás.

| Etapa | Resultado |
|---|---|
| `bootstrap` | APIs, cuatro identidades, buckets privados/versionados, repositorio y secreto vacío. |
| `service` | API HTTPS con Firebase Auth en la aplicación, recursos acotados y startup probe `/health`. |
| `jobs` | Entrenador y promotor separados, timeout/reintentos y permisos de invocación limitados. |
| `schedule` | **Activa** el calendario mensual; ejecutar solo al terminar las comprobaciones reales. |

Se requiere `gcloud` y una cuenta con permisos de administración para preparar
infraestructura; entrar con correo/contraseña en la app no concede esos permisos.
Agregar el valor TomTom al secreto por una vía segura, fuera del repositorio.

Las identidades runtime usan ADC, no el JSON local de desarrollo. API: Firestore
lectura/escritura, consulta de usuarios Auth y lectura de modelos/secreto; entrenador:
Firestore lectura, datos/modelos lectura, candidatos creación y registro de corridas;
promotor: lectura de candidatos/modelos, creación de versiones aprobadas, registro,
actualización del servicio y `actAs` únicamente sobre la identidad API; Scheduler:
invocación del Job de entrenamiento. API y entrenador no pueden desplegar revisiones.

**IAM de servidor no filtra por colección mediante las reglas Firestore.** Sus roles
tienen alcance de base/proyecto; las restricciones finas las aplica el código y se
reduce el alcance separando identidades y buckets. Las reglas móviles existentes
siguen negando escrituras de historial/feedback/caché. Ningún registro de operación
se expone a la app.

La región de ejemplo es `us-central1`. Verificar la ubicación de Firestore, costos
de transferencia y requisitos del proyecto antes de crear buckets. Son recursos
facturables aunque tengan cuotas gratuitas. Configurar alertas de gasto, vigilar
lecturas/listeners, versiones de blobs/imágenes y duración de entrenamientos; no
subir los videos. Una alerta de gasto no es por sí sola un corte automático.

## 5. Fuente del entrenamiento y benchmark mensual

Publicar una copia revisada de `training-spec.example.json` como objeto inmutable
en el bucket datasets. `history` apunta al CSV consolidado completo de 18 columnas
con generación y hash. Puede incluir `coverage` con el manifiesto verificado, y
`benchmark` con un JSON independiente fijado por generación/hash. Configurar el
descriptor del spec en `config` y en las variables `ARRIBO_TRAINING_SPEC_*` del Job.

Si no se proporciona benchmark, se construye con las jornadas más recientes del
feedback real (20% final de jornadas; mínimo 30 casos por defecto). Se exige al
menos dos jornadas y las observaciones previas de cada consulta son causales.
**Sin suficiente feedback, se registra `skipped`, no se inventan métricas.** Para
la primera aprobación puede proporcionarse un benchmark independiente de cámara.

Formato del benchmark:

```json
{
  "format": "arribo-benchmark-v1",
  "purpose": "monthly",
  "simulation": false,
  "cases": [{
    "sample_id": "ID_ESTABLE_REAL",
    "station_id": "rio_consulado",
    "query_at": "2026-10-20T15:00:00Z",
    "context": {"hour": 9, "day_of_week": 1, "is_weekend": 0, "is_holiday": 0},
    "target": 12,
    "simulation": false,
    "observations": []
  }]
}
```

Este JSON solo explica el formato; no constituye una observación real ni debe
publicarse como benchmark. Los IDs deben coincidir con los de las fuentes para
poder excluirlos. En cámara se excluye también el grupo del mismo bus. No usar la
prueba final de tesis. No reutilizar indefinidamente un benchmark seleccionado
por conveniencia: registrar su hash, cobertura y reutilización y renovar sus
jornadas sin mirar los errores para elegir los casos.

Cada Job congela feedback disponible antes del primer día del periodo a las 00:00
de Ciudad de México, junto con spec y modelo vigente. El benchmark es posterior
a todo dato admitido para ajuste/calibración. Los datos posteriores a su consulta
más antigua quedan fuera del ajuste; no se usa información futura para mejorar el
MAE retrospectivo. Los casos comparados son idénticos, pero cada modelo aplica sus
propios escaladores/respaldos. Artefactos antiguos sin IDs de train/calibración o
sin trazabilidad comparable deben revisarse, no asumirse compatibles.

Se conservan centinelas con peso cero. Los usuarios de cámara pesan 1 y feedback
0.5; las tres variantes de entrenamiento comparten ese peso total. Los pasos de
feedback no reciben `bus_arrival` ficticio. Fuera de dominio/simulación/duplicados
se excluyen; esperas altas válidas se señalan para revisión, no se borran por una
regla estadística automática. Calendario/clima mantienen las siete variables; no
se añaden tráfico ni contingencia al LSTM. Los respaldos y escaladores solo usan train.

## 6. Corridas, reintentos y promoción

El Job real se ejecutará con `--mode cloud --project arribo-68326
--confirm-dev-project arribo-68326`. Sin esa selección explícita, se ejecutan
fixtures locales. Cada periodo/spec identifica una corrida; cambiar datos del mismo
snapshot está prohibido. CAS por generación, lease renovable y comprobación de
propiedad impiden entrenamientos concurrentes y publicación tras perder el bloqueo.
Un fallo registra estado y un reintento crea otra versión de candidato. Un snapshot
completado se reutiliza. Estados: snapshot, training, evaluating, candidate,
evaluated, skipped o failed. Los snapshots contienen datos privados: nunca hacerlos
públicos ni incluirlos en el paquete que descarga la API.

El entrenador no aprueba ni despliega. El promotor vuelve a evaluar el candidato
frente al vigente actual. Solo admite mejoras estrictas del MAE operativo, MAE < 5
en operación y sin feedback, cobertura completa, elegibilidad y datos reales. Un
empate, error o incompatibilidad conserva el vigente. Primera aprobación: manual.

Para una corrida real evaluada, el operador podrá ejecutar:

```sh
Backend/.venv/bin/python Modelo/promote_job.py --execute --confirm-dev-project arribo-68326 --run-id ID_CORRIDA --manual-initial
```

Requiere variables de mantenimiento y ADC de la identidad autorizada. Sin
`--manual-initial`, no se permite la primera publicación. La API debe existir
previamente: puede arrancar sin modelo y devolver `MODEL_NOT_READY` hasta esa
primera aprobación. Para adoptar un paquete entrenado inicialmente, primero
registrarlo como candidato de una corrida evaluada con benchmark independiente;
no editar simplemente `approved=true` sin evidencia.

Registro administrativo de ese primer candidato, sin desplegarlo:

```sh
Backend/.venv/bin/python Modelo/promote_job.py --execute --confirm-dev-project arribo-68326 --register-only --run-id inicial-v1 --candidate Modelo/output/candidato-v1 --benchmark /ruta/privada/benchmark-mensual.json
```

Requiere ADC del responsable con creación en candidatos y escritura en ops, variables
`ARRIBO_CANDIDATES_URI`/`ARRIBO_REGISTRY_URI` y `GOOGLE_CLOUD_PROJECT`. El registro
calcula la evaluación y crea una corrida no desplegada; luego se usa el comando
de aprobación manual con `--run-id inicial-v1 --manual-initial`.

Promoción automática desactivada inicialmente en config y Job. Tras aprobar el
primer modelo, probar un ciclo real y comprobar recuperación, configurar
`auto_promotion=true`, actualizar Jobs y crear el calendario. El entrenador solo
puede invocar el Job promotor, sin overrides; el promotor usa su propia identidad
y vuelve a comprobar métricas y suspensión. Scheduler llama mensualmente al
entrenador a las 03:00 del día 1 mediante OAuth. No se necesita abrir la app.

## 7. Rollback y límites

Antes del cambio se guardan tráfico y paquete anteriores; nueva revisión fijada
por digest, sin tráfico, comprobación `/ready`, transferencia y segunda comprobación.
Si falla, se devuelve tráfico y se suspenden promociones. Un intento interrumpido
en el cambio de tráfico se recupera sin aprobar una segunda versión a ciegas.

Rollback administrativo, cuando existe una revisión anterior registrada:

```sh
Backend/.venv/bin/python Modelo/promote_job.py --execute --confirm-dev-project arribo-68326 --rollback
```

Verifica disponibilidad de la anterior, restaura su tráfico y deja suspensión
persistente. Revisar registros y eliminar la suspensión solo tras identificar y
corregir el fallo, mediante el operador autorizado. No borrar versiones anteriores.
Las revisiones etiquetadas de Cloud Run se pueden verificar antes de mover tráfico.
La adopción inicial exige pruebas autenticadas manuales app/API; el chequeo automático
de promoción comprueba carga e identidad con `/ready`, no suplanta usuarios ni crea
consultas de prueba reales. No afirmar que esto verifica toda la app automáticamente.

Pendientes reales: resolución/build Linux (Docker apagado), Keras real (dependencias
ML no instaladas), primera aprobación, infraestructura/credenciales/facturación,
pruebas físicas, latencias, utilidad de A y ejecución real de B. Las dos pruebas de
autenticación móvil ya fallaban antes de E y no se modifica esa lógica.

Referencias: [Jobs y OAuth](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule),
[precondiciones Storage](https://docs.cloud.google.com/storage/docs/request-preconditions),
[rollback Cloud Run](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration).
