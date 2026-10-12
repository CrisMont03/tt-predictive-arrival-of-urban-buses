La lectura anticipada prepara imágenes en un hilo mientras YOLO y el tracking
trabajan en el principal. Se conservan los mismos frames (uno de cada tres), sus
timestamps y el orden de actualización de ByteTrack, buses y personas. Los videos
del lote siguen ejecutándose en orden para conservar las colas entre grabaciones.

Por defecto, `--prefetch-frames 2` activa una cola acotada a dos imágenes pendientes
y `--batch-size 1` mantiene una imagen por llamada a YOLO. No se lanzan varias
instancias del modelo ni se reparten segmentos del video entre procesos.

Desde `Procesamiento_de_videos`, comparar el mismo video en carpetas separadas:

```bash
VIDEO=$'/Volumes/SSD_Buffalo/NVR\u00A0IRVING_ch1_main_20251003050000_20251003060000.mp4'

# Referencia secuencial en esta versión.
python procesar_video_v4.py "$VIDEO" \
  --timestamp-inicio "2025-10-03 05:00:00" \
  --zonas zonas.json --modelo yolov8m.pt \
  --prefetch-frames 0 --batch-size 1 \
  --output output/benchmark_oct03/secuencial

# Lectura anticipada, con una imagen por llamada a YOLO.
python procesar_video_v4.py "$VIDEO" \
  --timestamp-inicio "2025-10-03 05:00:00" \
  --zonas zonas.json --modelo yolov8m.pt \
  --prefetch-frames 2 --batch-size 1 \
  --output output/benchmark_oct03/prefetch

# Prueba adicional con dos imágenes por llamada a YOLO.
python procesar_video_v4.py "$VIDEO" \
  --timestamp-inicio "2025-10-03 05:00:00" \
  --zonas zonas.json --modelo yolov8m.pt \
  --prefetch-frames 2 --batch-size 2 \
  --output output/benchmark_oct03/batch2
```

Cada ejecución genera `_v4_rendimiento.json`: tiempo total, tiempo del bucle,
lectura, espera de imágenes, llamadas al modelo y actualización de estado/video.
Los tiempos de lectura se solapan con el trabajo principal; no se deben sumar.
La medición de inferencia es el tiempo de llamada, que puede no incluir todo el
trabajo asíncrono de la GPU. `total_seg` es la referencia para comparar ejecuciones.

La cola y los lotes tienen límites independientes: también hay imágenes en el lote
activo, resultados del modelo y una imagen que el lector puede tener preparada.
Con 8 GB de memoria se recomienda comenzar con 2 de prefetch y lote 1; probar lote
2 después. Un lote mayor puede consumir más memoria o rendir peor. Si hay errores
de memoria, volver a `--batch-size 1`.

Antes de usar lotes para recopilar datos, comparar los CSV y los eventos contra la
referencia secuencial. Un cambio en el tamaño de lote puede producir pequeñas
diferencias numéricas que afecten detecciones cercanas a los umbrales. No se ha
medido todavía el ahorro real en este equipo. Mantener iguales modelo, parámetros,
video y opciones de exportación durante la comparación.

El lector propaga excepciones y se detiene al cerrar el procesamiento. Si OpenCV
devuelve fin de lectura antes del número de frames declarado, los resultados se
marcan como parciales en consola y en el archivo de rendimiento. No se reparan
ni se reintentan automáticamente errores de decodificación.

Las opciones funcionan también con `--videos` y `--timestamps-inicio`, junto con
`--diagnostico-buses`. La lectura anticipada no corrige saltos horarios entre
videos: procesar grabaciones consecutivas juntas para encadenar sus colas.

Pruebas de lectura, orden, lotes y cierre (sin dependencias de detección):

```bash
python3 -m unittest discover -s tests -p 'test_video_pipeline.py' -v
```
