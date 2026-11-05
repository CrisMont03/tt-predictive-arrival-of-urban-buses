# Predicción de la próxima llegada de bus: preparación y pruebas locales

Este recorrido aprende `siguiente bus_arrival − instante de consulta`. El modelo
publicado anteriormente aprendió esperas de personas registradas, concentradas
cerca de las llegadas. Los dos problemas no tienen la misma distribución de errores.

## Datos y continuidad

`Dataset/next_bus.py` lee únicamente los CSV `*_v4_dataset.csv` de los días 1–15
de octubre de 2025, en las carpetas `dataset` o `datasets`. Conserva hashes y
procedencia por archivo y línea. No modifica los CSV originales.

La continuidad dentro de cada jornada se fundamenta en la confirmación explícita
del responsable del dataset. Se registra en `coverage.json`; este código no vuelve
a analizar los videos. Se consideran completas las llegadas entre la primera y
la última observadas de cada día, incluso entre archivos. No se unen jornadas ni
se inventa un siguiente bus después del último registrado.

Se deduplican timestamps idénticos y se utilizan también centinelas. Cada minuto
dentro de la cobertura genera una consulta cuyo objetivo es el tiempo hasta la
siguiente llegada, incluido cero si coincide con ella. Son consultas derivadas de
sucesos reales, **no nuevas personas observadas ni nuevos buses independientes**.
Las esperas largas permanecen; el nuevo dominio es de 0 a 1,440 minutos, acorde
con la cobertura dentro de una jornada.

Las particiones son 1–9 para entrenamiento, 10–11 para validación y calibración,
y 12–15 para revisión retrospectiva. Estos últimos días ya fueron examinados
anteriormente: no se presentan como una nueva prueba ciega.

El clima se reutiliza del export enriquecido local existente; de él se leen solo
las variables climáticas por fecha/hora. Se guarda la copia de valores utilizados
y el hash de esa fuente. Las horas sin clima permanecen desconocidas.

## Referencia y LSTM

La referencia calcula espera restante media por estación, día de semana y hora,
con respaldo por estación/hora y finalmente estación. Es una estimación histórica,
no un horario oficial ni una ubicación del bus en vivo.

El nuevo contrato `arribo-next-bus-feedback-v1` conserva 10 pasos y 27 valores por
paso. La hora se expresa como hora decimal a partir del timestamp de cada ficha,
aunque el campo HTTP `context.hour` continúe siendo entero. El nuevo objetivo y
las unidades se declaran en los metadatos; los paquetes antiguos conservan su
contrato y su dominio.

Las nueve fichas históricas usan reportes disponibles o respaldos; la última
contiene la consulta. Los respaldos de entrenamiento solo usan intervalos ya
completados; validación e inferencia utilizan la tabla congelada de entrenamiento.
Los respaldos son valores estadísticos identificados como tales, no supuestas
observaciones anteriores al comienzo del video. El escalado se ajusta una sola
vez con entrenamiento, sin repetir el ajuste por cada escenario.

Las consultas por minuto no se convierten en feedback. Los escenarios usan
las esperas personales originales como aproximación a reportes, conocidas después
de la llegada, con 25/50/100 % de disponibilidad y retrasos de 0/5/15 minutos,
además del escenario sin reportes. Cada minuto comparte su peso entre diez
escenarios. La centinela aporta una llegada al objetivo, pero no inventa un reporte.
La preparación y la API limitan la selección de recibos al mismo horizonte de dos
horas; dentro de ese snapshot, cada ficha respeta su propio corte temporal.

Se comparan RMSprop 0.0003 y Adam 0.001 con 16 unidades LSTM y una capa densa de
16, semilla 2026050, batch 32, hasta 50 épocas, sin barajar y paciencia 6 con
restauración de mejores pesos. Se selecciona por MAE operativo de validación y,
en empate, por MAE sin feedback. El optimizador elegido se repite con 2026051 y
2026052; el representante sigue siendo 2026050, sin elegir semilla por la revisión
retrospectiva. La consolidación final repite esa receta fija y verifica que el
MAE de validación no haya cambiado, conservando snapshots del código.

## Ejecutar sin modificar GCP

Desde `Codigo`, utilizar una salida nueva en cada experimento:

```bash
Modelo/.venv/bin/python Modelo/next_bus.py --stage prepare --output Modelo/output/proximo-bus-local
Modelo/.venv/bin/python Modelo/next_bus.py --stage search --output Modelo/output/proximo-bus-local
Modelo/.venv/bin/python Modelo/next_bus.py --stage finalize --output Modelo/output/proximo-bus-local
Modelo/.venv/bin/python Modelo/next_bus.py --stage evaluate --output Modelo/output/proximo-bus-local
Modelo/.venv/bin/python Modelo/verify_next_bus.py --output Modelo/output/proximo-bus-local
```

El entorno local necesita las dependencias ML existentes. La preparación no usa
Firebase ni modifica Storage; entrenamiento y evaluación se ejecutan en CPU. El
argumento `--weather` permite fijar otro export enriquecido y `--csv-root` otra
ubicación de los CSV diarios autorizados. El representante anterior usado como
diagnóstico se fija con `--legacy`.

Para consultar localmente un candidato sin aprobarlo ni escribir historial:

```bash
Modelo/.venv/bin/python Modelo/preview_next_bus.py \
  --package Modelo/output/proximo-bus-local/final/historical \
  --at '2026-11-04T17:30:00-06:00'
```

Para el LSTM, cambiar `historical` por la carpeta del candidato dentro de `final`.
Se pueden pasar `--reports` y `--context` como JSON locales para reproducir un caso.
Sin contexto guardado, el clima permanece desconocido. La herramienta identifica
el resultado como prueba local de candidato y está bloqueada dentro de Cloud Run.

## Evaluación y archivos

La salida contiene catálogo de llegadas, consultas, continuidad declarada,
fuentes con hashes, clima reutilizado, tensores de cada escenario, referencia y
candidatos. `final/selection.json` fija las opciones antes de la evaluación final;
los candidatos conservan `approved=false` y `eligible=false`.

`final/evaluation/comparison.csv` y `.json` comparan referencia y LSTM con/sin
feedback. También incluyen el modelo publicado sin feedback como diagnóstico;
`legacy-operational.json` añade su comparación con los mismos reportes parciales.
Los JSON incluyen MAE, RMSE, R², cobertura/amplitud del intervalo, errores por hora
y por esperas de 0–15, 15–45, 45–90 y más de 90 minutos, además de jornadas e
intervalos distintos. No se ocultan predicciones ausentes ni se descartan objetivos
por superar el dominio del modelo anterior.

Los intervalos se calibran con el percentil 90 del error absoluto de validación;
la cobertura retrospectiva puede ser inferior. Un R² mayor o predicciones más
largas no acreditan por sí solos que se cumpla MAE menor de cinco minutos.

Cada LSTM incluye `model.keras`, metadatos, historial y snapshots de código. La
referencia incluye `historical.json` y metadatos. La auditoría comprueba integridad
de CSV y pesos previos, igualdad offline/online y conservación de las restricciones
de aprobación. Los resultados y pesos permanecen bajo `Modelo/output`, ignorado
por Git; el código operativo y sus pruebas se conservan separados.

## Integración preparada y activación pendiente

La API y la app reconocen `prediction_target=next_bus_wait`, el estimador histórico
o LSTM y `estimated_arrival_at`. La caché resta el tiempo transcurrido de la espera
y del intervalo, y rechaza una llegada ya vencida. El historial antiguo sigue
siendo legible; el dominio de sus predicciones continúa siendo 45 minutos y su
formulario de feedback conserva el límite anterior de 180.

El adaptador de mantenimiento acepta explícitamente continuidad confirmada y
combina consultas históricas con feedback real cuyo inicio se confirmó en la
consulta. No reconstruye cuadrículas entre reportes parciales. Los datos del
benchmark se excluyen de ajuste y calibración y se conserva el peso 0.5 del
feedback. El cambio de objetivo requiere revisión manual: no puede publicarse
como una actualización mensual ordinaria del modelo personal anterior.

Las validaciones y el Dockerfile se prepararon localmente. **No se construyeron
ni publicaron nuevas imágenes en esta entrega y no cambió el servicio de GCP.**
Una futura activación requiere revisar resultados, aprobar un paquete con el nuevo
objetivo y dominio, desplegarlo, comprobarlo desde el teléfono y actualizar la
especificación mensual. No se reutiliza la excepción de aprobación del modelo
anterior ni se activa Scheduler.
