# Plan completo de desarrollo — ARRIBO · TT 2026-B050

## 1. Objetivo y estado de partida

Implementar un sistema de predicción de espera para Río Consulado–Misterios, CDMX, reutilizando la cámara existente y la app `Codigo/App_Movil`. El predictor de espera aún no está entrenado; YOLOv8m es un detector preentrenado que permite seguir obteniendo datos.

La inspección inicial encontró 773 registros en la base disponible: 772 esperas de usuarios y una fila centinela, de una única fecha (2025-06-02). La cantidad y diversidad deben ampliarse. La app existe con Expo SDK 57, React Native 0.86, TypeScript y Expo Router, pero sus pantallas iniciales son ejemplos.

### Contexto de los Markdown

| Archivos de KnowledgeBase | Aportación |
|---|---|
| 00-INDEX, 01-proyecto, 02-arquitectura-sistema | Requisitos, objetivos y arquitectura de cinco capas. |
| 03-pipeline-deteccion, 04-modelo-yolo, 05-tracker-bytetrack | Detección, filtros, tracking y continuidad entre grabaciones. |
| 06-roi-zonas, 07-logica-negocio | Polígonos y definición de las esperas observadas. |
| 08-dataset-schema, 09-consolidacion-enriquecimiento, 13-bases-de-datos | Datos base, enriquecimiento, SQLite y Firestore. |
| 10-despliegue-futuro | Modelo, servicios, aplicación y feedback. |
| 11-diagramas-uml, 12-diagramas-uml-prompts | Relaciones que deben actualizarse al cerrar los contratos. |
| 14-presentation, Plan-a-implementar | Identidad ARRIBO, exposición académica y propuesta inicial. |

## 2. Decisiones definitivas

- Usar la app existente, Expo Router y la identidad visual ARRIBO.
- Pantallas principales: Predicción, Historial, Alertas y Configuración; acceso y feedback como rutas auxiliares.
- Desarrollar con demostraciones explícitas mientras se construye el dataset. Nunca mezclar simulaciones con datos de entrenamiento.
- Toda consulta real pasa por FastAPI; el servidor reutiliza Firestore y registra el historial en hit y miss.
- Conservar las siete variables temporales y climáticas. Añadir entradas separadas para esperas anteriores, máscara de disponibilidad y contexto de llegadas de buses.
- Conservar las filas centinela en el dataset final y aprovecharlas en la construcción del contexto del entrenamiento.
- Tráfico y contingencia se almacenan como contexto; su incorporación como variables entrenables requiere evaluación futura.
- Una sola estación (`rio_consulado`), zona horaria `America/Mexico_City`. GPS opcional, advertencia a más de 200 m.

## 3. Implementación por pasos

### Paso 1. Alinear documentación y código

Registrar rutas, versiones, calibración y pendientes. El código v4 usa parámetros de geometría/color que difieren de algunas tablas; mantener la configuración realmente validada y documentar su versión. La exportación existente tiene doce columnas y pierde el identificador que permite reconocer centinelas. El script apunta a `Dataset/Coding`, mientras los artefactos disponibles están en `Dataset/Dataset-final`: parametrizar rutas y hacer explícita la base elegida.

**Salida:** inventario y rutas reproducibles; sin afirmar que el dataset o modelo ya están completos.

### Paso 2. Recopilar y validar datos

1. Crear manifiesto por video: origen/cámara, estación, inicio, duración, resolución, continuidad y configuración.
2. Procesar videos y encadenar únicamente grabaciones consecutivas de la misma cámara.
3. Comparar muestras contra anotaciones manuales de llegadas, salidas y esperas.
4. Revisar salidas previas al bus, cambios de ID, personas que permanecen y cortes entre videos.
5. Medir cobertura por hora, fecha, día de semana, lluvia y festivo; priorizar vacíos de cobertura.

**Salida:** reporte de calidad y cobertura. 5,000 registros es una meta, no garantía de generalización.

### Paso 3. Consolidar, enriquecer y exportar el dataset completo

1. Mantener eventos base y enriquecimiento separado en SQLite, conservando procedencia.
2. Garantizar idempotencia por origen y evento; mantener la copia anterior antes de migrar artefactos.
3. Reintentar enriquecimiento incompleto aunque exista la fila, sin convertir datos meteorológicos ausentes en cero.
4. Enriquecer también filas centinela por fecha y hora de `bus_arrival`.
5. Exportar **todos** los registros, con las doce columnas existentes y `track_id_persona`, `video_source`, `record_type`, `wait_observed`, `station_id` y `camera_id`.
6. Usar `record_type=user_wait` para usuarios y `bus_only` para centinelas; conservar `track_id_persona=-1`, timestamps del bus y esperas `0.0` por compatibilidad.
7. La máscara `wait_observed=0` diferencia ausencia de una espera observada de una espera real de cero.

**Salida:** dataset completo con centinelas, manifiesto y conteos comprobados. No filtrar centinelas al exportar.

### Paso 4. Definir contratos

`GET /health`: disponibilidad y estado del modelo. `POST /predict`: estación y request_id, autenticación, contexto, caché e historial. `POST /feedback`: query_id y actual_min, validación de propiedad y recuperación del snapshot original.

Respuesta de predicción: query_id, station_id, waiting_time_min, confidence_interval, model_version, timestamp, expires_at, source (mock/cache/model), context y simulation. Mantener errores `{code,message}` para autenticación, validación, indisponibilidad y timeout. El backend calcula calendario CDMX. Idempotencia: mismo uid/request_id devuelve la consulta original.

**Salida:** OpenAPI y tipos TypeScript equivalentes.

### Paso 5. Adaptar App_Movil

Transformar el layout raíz en Stack; grupos `(auth)` y `(tabs)`, con pestañas JavaScript. Componentes/hooks/servicios fuera de `src/app`. Usar ARRIBO: azul de acción, tarjetas, fondos claro/oscuro, etiquetas e iconos accesibles. Mantener áreas seguras, texto ampliado y formularios compatibles con teclado.

**Salida:** navegación móvil funcional sobre el proyecto existente.

### Paso 6. Construir las pantallas

| Pantalla | Contenido |
|---|---|
| Predicción | Estación, consulta explícita, minutos, intervalo, actualización, contexto y acción de feedback. |
| Historial | Consultas personales, promedio de predicciones por hora, cantidad y esperas reportadas diferenciadas. |
| Alertas | Listener, tipo, severidad, publicación y expiración. |
| Configuración | Cuenta, estación, GPS opcional, notificaciones y logout. |
| Acceso | Login, registro y recuperación de contraseña por correo. |
| Feedback | Consulta original, ingreso numérico, confirmación y envío idempotente. |

Implementar carga, vacío, error recuperable, sin conexión y antigüedad de datos. Las notificaciones push permanecen opcionales hasta configurar un development build; el toggle no debe afirmar que funcionan antes de configurarlas.

### Paso 7. Trabajar sin modelo

Crear adaptadores locales con escenarios deterministas de éxito, demora, error y ausencia de datos. Identificar todos los resultados como demostración. Preparar backend mock con contrato definitivo. Producción sin artefacto devolverá `MODEL_NOT_READY`.

### Paso 8. Firebase y persistencia

Firebase JS SDK para Auth/Firestore y Expo Go. Perfil creado explícitamente después del registro, sesión persistente y recuperación ante escritura fallida. Colecciones: users/{uid}, predictions/{cache_key}, query_history/{uid}/queries/{query_id}, feedback/{uid}/entries/{query_id}, alerts/{alert_id}. Backend escribe predicciones, historial y feedback validado; app modifica perfil/preferencias. Almacenamiento local por usuario y cola de reportes idempotentes. Reglas rechazan acceso cruzado y escrituras del cliente en colecciones administradas.

### Paso 9. Backend, caché y fuentes

Cache key: estación, fecha/hora local, versión y revisión de contexto. Comprobar expires_at antes de utilizar caché; TTL es limpieza, no validación. Guardar snapshots permanentes en historial. Consultas externas con timeouts y datos ausentes explícitos; alertas ambientales con comprobación periódica y estado desactualizado. Herramienta administrativa autenticada para publicar/retirar alertas.

### Paso 10. Preparar y entrenar el predictor

1. EDA, promedio histórico y XGBoost como baselines.
2. Historial de llegadas de **todos los buses**, incluidos buses sin pasajeros. Deduplicar filas de pasajeros por cámara/estación/timestamp del bus.
3. Generar tiempo desde el último bus y últimos intervalos, usando solo buses ya conocidos antes de la consulta. Registrar huecos entre grabaciones: no inferir continuidad a través de cobertura faltante.
4. LSTM: siete variables históricas/actuales, esperas anteriores con máscara y contexto de buses. Elegir ventana 10/20/30 por validación temporal.
5. Centinelas participan construyendo el contexto; la pérdida del regresor de espera solo usa etiquetas `user_wait` con espera observada.
6. Partición 70/15/15 por bloques cronológicos de jornadas/eventos. Preprocesadores ajustados solo con entrenamiento; no usar resultados posteriores a cada consulta.
7. Simular falta de contexto también en entrenamiento. Sin observaciones reales actuales, usar promedios históricos y máscaras explícitas.
8. Evaluar MAE (<5 min), RMSE (<7 min), R² (>0.70), errores por franja y cobertura del intervalo. Versionar modelo, esquema, preprocesadores, ventana, residuos, métricas y dataset.

**Salida:** artefacto validado. No publicar un LSTM por cumplir únicamente un número de filas.

### Paso 11. Aprendizaje continuo

Mecanismo A: últimas N observaciones válidas de estación recibidas antes de la consulta, dentro de las dos horas previas; nunca tratar predicciones como observaciones. Mecanismo B mensual: combinar histórico y feedback validado (peso inicial feedback=0.5, cámara=1.0), evaluar con validación operativa independiente y promover solo si mejora y cumple metas. Mantener rollback. Reportes de más de 45 min se conservan y marcan fuera del dominio inicial. Tráfico/contingencia como variables entrenables es fase futura con ≥2,000 reportes completos y nueva evaluación.

### Paso 12. Despliegue y cierre

Cloud Run, Storage con artefactos inmutables, Secret Manager, Scheduler/Jobs, índices, TTL, reglas y permisos mínimos. Medir latencia/error/cache/model_version, incluyendo cold-start y objetivo de respuesta <3 s. Generar builds EAS Android/iOS; push opcional con expo-notifications y Expo Push Service. Actualizar UML, presentación, manuales y métricas con evidencia real.

## 4. Aceptación

- Dataset final incluye usuarios y centinelas, procedencia, tipo y máscara.
- Buses sin pasajeros contribuyen a intervalos; un bus con varios pasajeros cuenta una sola vez.
- Ningún cero centinela se interpreta como etiqueta de espera de un usuario.
- Entrenamiento e inferencia comparten entradas causales; pruebas cubren huecos y falta de contexto.
- Acceso, cuatro pantallas, feedback, alertas y preferencias funcionan con demostración antes del modelo.
- Historial guarda hit/miss; reintentos no duplican consultas ni reportes.
- Aislamiento por uid, red intermitente, sesión vencida y permisos denegados comprobados.
- Lint y TypeScript en cada incremento; pruebas backend/dataset y métricas ML.

## 5. Orden y condiciones de entrega

Datos/calidad y app/contratos/backend pueden avanzar en paralelo. La integración de inferencia requiere dataset diverso, modelo validado y fuentes de contexto disponibles. Firebase, builds y despliegue necesitan configuración de las cuentas del equipo. El código y herramientas locales no equivalen a infraestructura desplegada.

## Referencias técnicas

- [Expo SDK 57](https://docs.expo.dev/versions/v57.0.0/)
- [Firebase con Expo Go](https://docs.expo.dev/guides/using-firebase/)
- [Layouts Expo Router](https://docs.expo.dev/router/basics/navigation-layouts/)
- [Notificaciones](https://docs.expo.dev/versions/v57.0.0/sdk/notifications/)
- [TTL Firestore](https://firebase.google.com/docs/firestore/ttl)

## 6. Estado de implementación local — 2026-10-05

Implementados: exportación completa de 18 columnas con centinelas; enriquecimiento reintentable; secuencias con máscaras y reporte de cobertura; pantallas ARRIBO y modo demo; adaptadores Auth/Firestore, historial y cola offline; contratos FastAPI, caché, idempotencia y snapshots; reglas/índices/TTL preparados; entrenador de candidatos y cargador de artefacto aprobado; CLI administrativa de alertas.

Verificado: CSV con 773 registros, incluidos 772 usuarios y 1 centinela, y 50 buses únicos; una sola jornada. Pruebas de dataset/backend/utilidades, TypeScript, lint, compatibilidad Expo y exportación de bundles Android/iOS/web. La base SQLite original se conserva sin cambios.

Pendiente: ampliar/anotar datos y verificar manifiestos; ejecutar/validar entrenamiento real y revisar candidatos; probar reglas en emuladores e integración con cuentas reales; seleccionar fuente oficial de contingencia; configurar y desplegar Firebase/Cloud Run/EAS; push; reentrenamiento mensual; pruebas físicas, métricas de operación y actualización final de UML/presentación. La auditoría npm encontró avisos transitivos que deben revisarse antes de publicar. No hay modelo entrenado, métricas ML reales ni servicios desplegados.

Consultar `ModeloDeteccion/Codigo/README.md` para comandos, restricciones y estado técnico. El plan conserva las etapas futuras; esta implementación no las declara completadas por anticipado.
