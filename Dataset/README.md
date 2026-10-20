# Sección B: preparar el entrenamiento y la inferencia

Esta etapa construye la misma receta de entrada para el dataset y FastAPI.
No entrena un modelo, no conecta cámaras, no publica Firebase y no modifica videos.

## 1. Datos fuente y consolidación

Los CSV originales están en `Codigo/Procesamiento_de_videos/output/dataset`.
El antiguo ejemplo de 773 filas no representa el dataset en construcción.
Las rutas de los comandos siguientes son relativas a `Codigo`.

Para consolidar y enriquecer, usar un entorno con `requests` y `holidays`:

```sh
Backend/.venv/bin/python Dataset/consolidar_dataset.py consolidar
Backend/.venv/bin/python Dataset/consolidar_dataset.py enriquecer
Backend/.venv/bin/python Dataset/consolidar_dataset.py exportar
```

Estos comandos escriben en `Dataset/output/dataset_maestro.db` y
`Dataset/output/dataset_final.csv`, no en los CSV fuente. Son instrucciones para
ejecutar cuando corresponda; la implementación de B no los ejecutó sobre los datos reales.
La consolidación es idempotente; el enriquecimiento consulta servicios externos;
la exportación reemplaza el CSV de destino. Para separar una nueva corrida, indicar
`--db`, `--csv-final` y `--resultados` **antes** del subcomando.
No seleccionar accidentalmente una base de pruebas antigua.

## 2. Generar secuencias, sin entrenar

```sh
Backend/.venv/bin/python Dataset/preparar_modelo.py \
  --csv Dataset/output/dataset_final.csv \
  --output Dataset/output/secuencias-v2-001 \
  --window 10
```

La carpeta de salida debe ser nueva. El comando genera:

- `sequences.jsonl`: todos los registros, incluidas centinelas, objetivos, máscaras y tres escenarios.
- `quality.json`: contrato, conteos, particiones, respaldos de entrenamiento y hashes de procedencia.

Ventanas permitidas: 10, 20 y 30. El valor inicial es 10; no implica que sea el óptimo.
`can_train` solo indica que existen particiones utilizables. No acredita diversidad,
la meta de recopilación ni precisión. `meets_collection_goal` comprueba 5,000 esperas
personales utilizables; las centinelas no incrementan esa cuenta.

## 3. Las siete variables y las entradas adicionales

Orden fijo: `hour`, `day_of_week`, `is_weekend`, `is_holiday`,
`precipitation_mm`, `temp_c`, `humidity`.
Unidades: hora 0–23, lunes=0 a domingo=6, dos indicadores binarios, mm, °C y %.
Los timestamps sin zona del video se interpretan en `America/Mexico_City` y se
normalizan a UTC. La hora y el día siempre se derivan del instante de consulta.

Por paso se concatenan contexto + máscaras (14), espera + disponibilidad (2),
cuatro variables de buses + máscaras (8), y origen de espera (3): **27 valores**.
La forma es `(lote, N, 27)`. El LSTM recibe además `step_mask` con forma `(lote, N)`
y objetivo separado `(lote, 1)`. El esquema es `arribo-sequences-v2`, incompatible
con los modelos anteriores de 24 valores.

El origen es `[observada, respaldo, ausente]`. Una espera real de cero es conocida;
un cero de ausencia tiene disponibilidad 0. El último paso siempre tiene espera
desconocida. El relleno estructural tiene todos los campos a cero y se ignora mediante
la máscara explícita. No se escalan máscaras ni categorías.

## 4. Qué aportan las centinelas

`bus_only`, `track_id_persona=-1`, `wait_observed=0` identifica una llegada sin
pasajero observado. Se conserva con `loss_weight=0`, no como espera personal de cero.
`user_wait` con espera cero sí puede entrenar la salida.

Las llegadas de ambas clases permiten calcular minutos desde el último bus y tres
intervalos anteriores. Se deduplican por timestamp dentro del mismo bloque de
cobertura: sin identificador de bus común no se inventa una asociación entre horas
distintas. El contexto de un instante usa solo llegadas estrictamente anteriores.

## 5. Continuidad y disponibilidad

Sin manifiesto se aíslan estación, cámara, video, jornada y partición. La cobertura
conservadora empieza en el primer evento conocido. No se admite una espera que
cruce jornadas sin cobertura verificada.
`manifest.example.csv` es solo una plantilla con `verified=0`: no prueba continuidad.
Tras verificar los metrajes, se puede añadir `--manifest ruta.csv`.
Los intervalos declarados consecutivos deben coincidir con tolerancia de dos segundos;
se valida el inicio de la consulta y la finalización dentro de la cobertura del grupo.

Se separan jornadas cronológicamente (~70/15/15), manteniendo juntos pasajeros del
mismo bus. Las ventanas se reinician al cambiar de partición. Si una espera de
validación/prueba empieza antes del cierre de la partición anterior, se conserva
para auditoría pero se excluye del entrenamiento y de las métricas.

Una espera de video se conoce al completarse, en `bus_arrival`. Un feedback se
conoce en `received_at`, conservando contexto y `query_at` de la consulta original.
Para predecir en `t`, ambos tiempos deben ser anteriores a `t`; se usa un horizonte
de dos horas, solo la misma estación y hasta `N−1` consultas distintas.
Simulaciones y esperas fuera de 0–45 minutos no entran al contexto operativo.

## 6. Promedios y escalado sin información futura

Los respaldos siguen esta jerarquía por estación: día/hora, hora, promedio global.
En entrenamiento se actualizan solo con esperas de entrenamiento ya completadas
antes de cada consulta: nunca con su propio objetivo ni con una llegada futura.
En validación/prueba y producción se congelan los respaldos del entrenamiento.

Los pasos de respaldo se distribuyen uniformemente entre el inicio del horizonte
permitido y la observación real más antigua (o la consulta si no hay reportes), sin
tocar los extremos. Se usa el calendario de cada paso, clima y buses desconocidos.
Si no hay promedio o espacio válido, se utiliza relleno estructural, no una espera
inventada. Los pasos sintéticos no son observaciones reales.

El escalador usa únicamente `train`, cada contexto de consulta una vez y las esperas
reales únicas para la escala de espera. No cuenta ventanas superpuestas como nuevas
mediciones. Los valores ausentes quedan a cero después de transformar; los respaldos
sí se transforman con la escala de espera de entrenamiento.

## 7. Mecanismo A sin cámaras en producción

El backend recupera feedback recibido a tiempo y lo coloca en los pasos históricos
con su propia espera, no con otra espera retrasada. Los pesos del modelo no cambian.
No se deduce una llegada exacta a partir de `query_at + actual_min` ni de `received_at`.
La cámara solo produjo el dataset histórico.

Cada ejemplo contiene `inputs` (llegadas históricas conocidas), `operational_inputs`
(reportes conocidos, buses desconocidos) y `cold_inputs` (sin reportes ni buses,
con respaldos disponibles). El entrenador combina los tres escenarios y evalúa
cada uno por separado. XGBoost recibe la ventana completa y su máscara, no solo
el paso actual. El escenario operativo es obligatorio para evaluar el modelo.

La caché contempla contrato, entradas preparadas y reportes seleccionados. Un
feedback nuevo relevante cambia su identidad. Sin artefacto aprobado, una consulta
nueva conserva la respuesta `503 MODEL_NOT_READY`.

## 8. Entrenar y conectar el modelo más adelante

Instalar `Modelo/requirements.txt` en el entorno ML elegido y ejecutar, cuando los
datos estén revisados:

```sh
Modelo/.venv/bin/python Modelo/entrenar.py \
  --data Dataset/output/secuencias-v2-001 \
  --output Modelo/output/candidato-v2-001 --version candidato-v2-001
```

La salida nueva contendrá `model.keras`, `xgboost.json` y `metadata.json`.
El paquete conserva escaladores, respaldos, contrato, hashes, métricas y radio de
intervalo calibrado con validación operativa. No se ajusta el modelo con prueba.
La elegibilidad requiere la meta de recopilación y MAE de validación <5 min en los
escenarios operativo y sin feedback. Es una condición necesaria, no aprobación automática.
El candidato siempre se guarda con `approved=false` y requiere revisión independiente
del conjunto de prueba, franjas, cobertura y utilidad del feedback antes de aprobarlo.

Para servirlo: instalar `Backend/requirements-model.txt` y configurar
`ARRIBO_MODEL_DIR` al directorio aprobado. FastAPI comprueba contrato, hashes,
escaladores y formas de las dos entradas. No basta con copiar cualquier archivo LSTM.
Faltará verificar precisión real sin llegadas en vivo, arranque sin feedback,
efecto de los reportes y recorrido app → consulta → historial → feedback.
No se garantiza que las centinelas o el feedback mejoren el MAE: se debe medir.

## 9. Pruebas y límites de esta entrega

```sh
# Desde Codigo/Backend
.venv/bin/python -m pytest -q
# Desde Codigo
Backend/.venv/bin/python -m unittest discover -s Dataset/tests -v
```

Las pruebas cubren causalidad, ventanas, centinelas, continuidad, respaldos, escalado,
igualdad offline/online, caché y rechazo de artefactos incompatibles.
La prueba de guardado/carga y máscara real de Keras se omite si TensorFlow/NumPy
no están instalados. Instalar el entorno ML y ejecutar esa prueba antes de entrenar.
Las pruebas de Firestore requieren su emulador y no se publican ni ejecutan sobre
el proyecto real por el hecho de correr estas pruebas unitarias.

La preparación usa el clima horario histórico disponible; debe revisarse su
comparabilidad con el contexto meteorológico utilizado en producción al evaluar.
El cierre de B no implica modelo entrenado, MAE alcanzado, despliegue, reentrenamiento
mensual, cambios de app ni validación física con Firebase real.
