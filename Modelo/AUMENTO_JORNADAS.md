# Aumento del próximo bus por jornadas completas

Este experimento local genera escenarios del 16 al 31 de octubre de 2025 a partir
solo de jornadas reales del 1–9. Compara el LSTM real y el ampliado manteniendo los
mismos respaldos, escaladores y casos reales. No publica recursos ni aprueba modelos.

## Generación y clima

El generador elige una jornada padre con el mismo día de semana, usando semilla
2026053. Aplica a pasajeros y buses una misma transformación temporal creciente,
con límites fijos 05:00 y 21:00. Los desplazamientos de las llegadas se limitan a
cinco minutos y al 5 % de las distancias a los anclajes vecinos; los intervalos
consecutivos quedan aproximadamente entre 90 % y 110 % del original. Se preservan
los intervalos largos y los buses sin pasajeros. No se añaden llegadas para ocupar
los extremos de la jornada ni se unen escenarios diferentes.

Los CSV tienen exactamente doce columnas: `track_id_persona`, `arrival_user`,
`bus_arrival`, `waiting_time_min`, `waiting_time_seg`, `date`, `hour`, `day`,
`is_weekend`, `precipitation_mm`, `temp_c` y `humidity`. Los timestamps conservan
fecha y hora completas en Ciudad de México. Las esperas se recalculan desde los
timestamps, con precisión de hasta tres decimales en minutos y un decimal en
segundos. Las centinelas conservan ID −1 y espera cero; los demás IDs son únicos
dentro de cada jornada.

Se consulta Open-Meteo Historical con ERA5 para 2025-10-16..31, coordenadas de la
estación 19.4624, −99.1297 y zona `America/Mexico_City`. Temperatura en °C,
precipitación en mm y humedad en porcentaje se asignan a la hora de llegada de
la persona. Las consultas por minuto utilizan su propia hora climática.

Se conservan respuesta API, parámetros, fecha de descarga y hashes. Es clima de
reanálisis sobre una celda de ERA5: no una medición directa del parabús. La celda
retornada en esta entrega fue 19.5, −99.25. Que el clima sea histórico no convierte
las llegadas simuladas en observaciones reales ni acredita una relación causal
entre ambas variables. Se reintenta tres veces ante errores transitorios y se
detiene el enriquecimiento si faltan variables o unidades válidas.

Los CSV no incluyen columnas extra de procedencia: `manifest.json` y
`lineage.jsonl` vinculan cada archivo y fila con jornada padre, archivo/línea,
semilla, transformación y condición sintética. No copiar estos CSV a las carpetas
reservadas para datos reales. No cuentan como nuevas personas o buses observados.

## Preparación y entrenamiento

Cada jornada es un escenario independiente. Sus consultas se generan entre la
primera y la última llegada, sin recortar a 45 minutos. Ninguna consulta por minuto
se transforma en feedback. Solo las filas personales generan aproximaciones de
reportes con disponibilidad causal y retrasos, marcadas como sintéticas y admitidas
únicamente por el constructor offline explícito.

Se usa `arribo-next-bus-feedback-v1`, ventana 10, 27 valores, LSTM de 16 unidades,
capa densa 16 y Adam 0.001. Batch 32, máximo 50 épocas, sin barajar, paciencia 6,
restauración de mejores pesos y semillas 2026050/2026051/2026052. Se reparten los
pesos entre diez escenarios; el peso sintético total equivale a 0.5 veces el real.

Los tensores reales de entrenamiento se conservan como prefijo sin alteraciones.
Los respaldos y escaladores son los del experimento real. Para las fechas sintéticas
posteriores al día 9 se utiliza la tabla real congelada; nunca se calculan promedios
con las esperas sintéticas. Validación y retrospectiva conservan sus archivos de
tensores exactamente iguales a los de referencia.

La selección de familia se registra con el MAE operativo medio de validación de
las tres semillas, usando MAE sin feedback para desempatar y prefiriendo la familia
real en empate completo. La semilla representante queda fijada en 2026050. El
intervalo de cada modelo se calibra con el percentil 90 del error absoluto de los
mismos casos reales de validación. No se recalibra el modelo real ya existente.

## Interpretación temporal

Los casos de comparación son reales de los días 10–11; los días 12–15 se revisan
como retrospectiva adicional después de fijar la elección. Como los escenarios
sintéticos usan fechas y clima del 16–31, **esto es un experimento retrospectivo y
exploratorio**, no una prueba cronológica futura. Los padres del generador siguen
siendo exclusivamente los días 1–9. La verificación prospectiva necesita reportes
reales nuevos, posteriores al corte declarado del candidato.

Se reportan MAE, RMSE, R², cobertura y amplitud del intervalo, resultados por hora
y por bandas de espera, con reportes parciales y sin ellos. Las medias entre
semillas no son un ensemble de predicciones. Un aumento puede mejorar una métrica
y empeorar otra; la selección sigue el criterio fijado, sin elegir a partir de
los resultados retrospectivos.

## Comandos

Desde `Codigo`, usar una salida nueva; los argumentos por defecto fijan la
preparación y candidatos reales de `next-bus-20261105-001`:

```bash
Modelo/.venv/bin/python Dataset/augment_next_bus.py --output Modelo/output/aumento-jornadas/generated
Modelo/.venv/bin/python Modelo/augment_next_bus.py --stage prepare --output Modelo/output/aumento-jornadas
Modelo/.venv/bin/python Modelo/augment_next_bus.py --stage train --output Modelo/output/aumento-jornadas
Modelo/.venv/bin/python Modelo/augment_next_bus.py --stage evaluate --output Modelo/output/aumento-jornadas
Modelo/.venv/bin/python Modelo/verify_augment_next_bus.py --output Modelo/output/aumento-jornadas
```

La descarga necesita internet y no utiliza claves administrativas. Para reproducir
exactamente el clima, reutilizar `weather-response.json`: el generador admite
reanudar una carpeta que solo contenga ese archivo. Se valida que los parámetros
coincidan. Las salidas completas no se sobrescriben.

La entrega ejecutada está en `Modelo/output/next-bus-augmented-20261105-001`:
`generated` contiene los 16 CSV y metadatos climáticos/de procedencia; `preparation`
contiene los tensores y referencias congeladas; `runs` contiene los tres LSTM;
`evaluation` contiene comparación completa y elección fijada en validación.
`RESULTADOS.md` explica los resultados y `verification.json` registra las
comprobaciones. Datos y modelos están ignorados por Git. Los candidatos mantienen
`approved=false` y `eligible=false`; GCP permanece sin cambios.
