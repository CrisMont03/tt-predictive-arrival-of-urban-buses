# Contexto externo real: clima, tráfico y contingencias

## Alcance implementado

`app/context.py` consulta Open-Meteo, TomTom Traffic Flow y los comunicados oficiales
de la Comisión Ambiental de la Megalópolis (CAMe) en paralelo. Los valores siguen
en `context`; su procedencia se guarda por separado en `context_sources`, sin
añadir variables a las siete entradas contextuales de la preparación del modelo.

No se implementa un LSTM ficticio, no se cambian reglas o índices de Firestore y
no se publican alertas móviles automáticamente. Una contingencia en este contexto
no crea un documento en `alerts`: esa colección tiene su propio flujo administrativo.

## Tráfico: TomTom Traffic Flow

- Endpoint: `https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json`.
- Punto de consulta: Río Consulado–Misterios (`19.4624,-99.1297`); unidades km/h.
- Requiere `TOMTOM_API_KEY` configurada únicamente en el servidor.
- Se conserva la definición ya existente del contrato:
  `traffic_density = min(1, currentSpeed / freeFlowSpeed)`.
  **1 significa flujo libre; 0, velocidad actual cero.** No es un conteo de vehículos,
  una densidad física ni el cociente respecto del límite legal de velocidad.
- Se validan números finitos y rangos, velocidad libre positiva, confianza mínima,
  ausencia de cierre y coordenadas del segmento devuelto. Una carretera cerrada,
  respuesta inválida o confianza insuficiente produce `null`, no congestión inventada.
- La confianza mínima de `0.5` es una decisión configurable del proyecto, no una
  garantía de TomTom. La respuesta usa el segmento seleccionado por el proveedor
  para ese punto; sus coordenadas permiten revisar manualmente el sentido y la vía.
- Se conservan velocidades, confianza y geometría para auditoría. La clave no se
  guarda en Firestore ni en la respuesta. El logger INFO de HTTPX redacta `key=`;
  no habilites logs HTTP de bajo nivel que puedan revelar cabeceras o parámetros.

Fuente: [TomTom Flow Segment Data](https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-flow/flow-segment-data).

La geometría no es un número fijo: es la lista `segment_coordinates` devuelta por
TomTom. El punto de consulta sigue siendo `19.4624,-99.1297`, estilo `absolute`,
zoom `10`. Se añadió `geometry` con cantidad de puntos, extremos, punto más
cercano, distancia aproximada y orientación local de la línea (0° norte, 90° este).
`polyline_bearing_deg` describe el orden de coordenadas, no certifica el sentido
legal de circulación ni que los buses recorran esa calzada. Por eso
`travel_direction_verified` permanece en `false`.

La referencia `Extras/Sentido.png` muestra flechas de izquierda a derecha sobre
la vía lateral pegada al parabús y salida del Metro, no los carriles centrales.
Sin norte o dos puntos georreferenciados no se traduce esa perspectiva a un
rumbo cardinal de forma concluyente. No se cambió el punto TomTom ni se fijó un
sentido supuesto; queda pendiente contrastarlo con un mapa o destino confirmado.

## Contingencias: fuente oficial CAMe

Se consulta `https://www.gob.mx/comisionambiental/archivo/prensa?idiom=es-MX`.
Se eligió CAMe porque el propio portal Aire CDMX identifica a esa comisión como
responsable de activar y dar seguimiento al programa. La consulta directa a Aire
mostró problemas TLS en el entorno Python; no se desactiva TLS ni se bajan sus
requisitos de seguridad para sortearlos.

El archivo observado contiene artículos con título, fecha/hora y enlace; también
puede devolverse como fragmentos de HTML dentro de llamadas jQuery. El lector
decodifica sus cadenas sin ejecutar JavaScript. No utiliza resultados de un buscador
ni un sitio de noticias como fuente del estado en ejecución.

La interpretación es deliberadamente conservadora:

- `1`: último comunicado reconocido de activación, mantenimiento o continuidad
  de contingencia para la Zona Metropolitana del Valle de México completa.
- `0`: último comunicado reconocido de suspensión de contingencia para esa zona.
- `null`: petición fallida, 403/429, cambio de estructura, ausencia de comunicados
  relevantes, fechas inválidas/futuras, ámbito regional no
  resuelto, títulos contradictorios o suspensión parcial/ambigua.

Se selecciona la publicación más reciente por fecha/hora, no por la primera
coincidencia textual. Un comunicado regional nuevo bloquea la interpretación de
uno global anterior. No se deduce una contingencia de niveles de contaminación,
restricciones dominicales, del nombre de una alerta móvil ni de palabras en un menú.

Las horas sin offset del archivo se interpretan en `America/Mexico_City`. El campo
es **binario**, no almacena un nivel de contingencia. No resuelve automáticamente
episodios simultáneos por contaminante ni la aplicabilidad de zonas regionales.
La interpretación depende de un archivo público, no de una API oficial con SLA.
Por tanto, no debe presentarse como un servicio exhaustivo de avisos de seguridad.

Por decisión explícita del proyecto, la antigüedad ya no convierte una declaración
reconocida en `null`: se conserva el último estado publicado hasta encontrar un
comunicado posterior aplicable. Una suspensión del 13 de septiembre puede devolver
`0` en octubre; una activación antigua puede devolver `1` con la misma política.
Noticias posteriores sin relación con una declaración no sustituyen ese estado.

Esto representa **el último estado publicado reconocido, no una confirmación en
vivo**. Los metadatos incluyen `state_basis: latest_recognized_official_notice`,
`notice_age_hours`, `notice_is_old` y `warning: old_notice_carried_forward` cuando
corresponde. El umbral de 24 horas solo activa esa advertencia. No se trata de una
vigencia oficial y no se oculta la fecha original del aviso. La caché se actualiza
según la fecha de consulta, sin consultar una sola vez y asumir que es permanente.

Errores de red, bloqueos o documentos que no puedan interpretarse siguen dando
`null`; no se convierten en una suspensión. Continúan pendientes las contingencias
regionales y el seguimiento exhaustivo de episodios simultáneos. La nueva política
no convierte el archivo público en una API de estado vigente con SLA.

Fuentes: [archivo CAMe](https://www.gob.mx/comisionambiental/archivo/prensa?idiom=es-MX),
[programa de contingencias de Aire CDMX](https://aire.cdmx.gob.mx/default.php?opc=%27YqBhnmU=%27).

## Caché y fallos parciales

Los cachés son en memoria por proceso; no son persistencia compartida entre
instancias de Cloud Run. Defaults: clima 60 segundos (separado por hora local),
tráfico 60 segundos y contingencias 300 segundos. Un error se conserva solo 15
segundos para evitar peticiones repetidas inmediatas. No se devuelve un valor
vencido como válido tras un error. La caché de predicciones comprueba también la
vigencia de los metadatos de origen cuando están presentes.

El TTL de siete días descrito en la propuesta original no se implementa: podría
ocultar activaciones y suspensiones durante demasiado tiempo. Cada fuente falla
por separado; un problema con CAMe no borra el clima o tráfico disponibles. Las
peticiones tienen timeout de 3.5 segundos. No se siguen redirecciones arbitrarias.

## Persistencia y significado temporal

En una predicción exitosa, `context_sources` conserva por proveedor:

- `status`, `reason`, URL pública sin credenciales;
- `checked_at` y `valid_until` del resultado consultado o cacheado;
- fecha de la publicación CAMe y enlace al comunicado, cuando se identifican;
- observación horaria del clima y detalles del segmento TomTom, cuando existen.

Este snapshot acompaña al contexto en caché e historial. `/feedback` copia
`context` y `context_sources` de la consulta original y registra aparte
`received_at`. No consulta condiciones nuevas al reportar ni sustituye el contexto
original. Un reenvío conserva el primer reporte. Consultas antiguas o fixtures
sin procedencia siguen siendo compatibles; no se rellenan retroactivamente con
datos actuales ni se reclasifican como observaciones reales.

## Configuración y diagnóstico sin LSTM

Desde `Codigo/Backend`, en la misma terminal donde después inicies FastAPI:

```bash
export TOMTOM_API_KEY="TU_CLAVE_PRIVADA_DE_TOMTOM"
export TOMTOM_MIN_CONFIDENCE=0.5
export TOMTOM_CACHE_SECONDS=60
export ENV_ALERT_CACHE_SECONDS=300
export ENV_ALERT_OLD_NOTICE_HOURS=24

.venv/bin/python check_context.py
```

No pegues una clave real en conversaciones o capturas. También puedes establecerla
desde un mecanismo local de secretos sin mostrarla. El backend no carga por sí
solo un `.env`: estas variables deben estar en su proceso. Nunca uses
`EXPO_PUBLIC_TOMTOM_API_KEY` ni una clave dentro del código o la app. El ejemplo
`.env.example` solo documenta nombres y valores no secretos.

`ENV_ALERT_MAX_AGE_HOURS` pertenece a la política anterior y ya no invalida avisos.
El nombre vigente es `ENV_ALERT_OLD_NOTICE_HOURS`; solo controla la advertencia de
antigüedad. Si configuras las variables en `Backend/.env`, para probar sin exportarlas:

```bash
.venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv(".env", override=True); from check_context import main; raise SystemExit(main())'
```

Este comando carga el archivo únicamente en el proceso de diagnóstico. No cambia
la configuración de una instancia FastAPI que ya esté ejecutándose.

`check_context.py` consulta fuentes reales sin requerir modelo ni credenciales de
Firebase. No crea consultas, feedback, alertas ni datos del dataset. Muestra valores
y causas de ausencia; código de salida `0` si todas están disponibles, `2` si al
menos una no es verificable y `1` para un fallo general/configuración inválida.

El endpoint `/predict` mantiene `503 MODEL_NOT_READY` antes de consultar estos
proveedores si no hay modelo; no gasta solicitudes TomTom para fingir una predicción.
Por eso este diagnóstico permite probar las fuentes independientemente del LSTM.
Reinicia FastAPI después de cambiar código o variables: `run_development.py` no
usa recarga automática. No hace falta cambiar el `.env` de la app.

## Pruebas y resultado de la comprobación externa

```bash
.venv/bin/python -m pytest -q
```

La ejecución inicial tuvo 71 pruebas aprobadas y ocho de integración aprobadas
por separado. Se amplió la suite para verificar conservación de estados antiguos
0/1, reemplazo por declaraciones posteriores y conservación en feedback tras
reintentos. Las pruebas de integración siguen usando solo el emulador.

`tests/test_external_context.py` cubre activación/suspensión, fechas y zonas,
HTML y fragmentos jQuery, conflictos, esquema inválido, velocidades cero y límites,
confianza/cierre, errores HTTP y timeouts, cacheado/expiración, claves ausentes y
redacción del logger. Las respuestas de proveedores se controlan con MockTransport;
no son evidencia de acceso TomTom real. `tests/test_production.py` comprueba además
la conservación del contexto y su procedencia al enviar y repetir feedback.

En la comprobación externa de esta implementación se obtuvieron datos de clima
reales y se leyó el archivo CAMe. La suspensión más reciente reconocida estaba
fechada el 13 de septiembre de 2026; inicialmente se devolvió `null` por antigüedad.
Con la política nueva esa misma declaración reconocida devuelve `0`, conservando
su fecha y la advertencia de antigüedad; se comprobó nuevamente y devolvió `0`.
TomTom devolvió `missing_api_key` porque en la terminal de prueba no había clave.
Por tanto, queda pendiente verificar TomTom con una clave autorizada y contrastar
un comunicado ambiental reciente con la autoridad. No se afirma que las dos
variables ya estuvieran disponibles de forma completa en aquella comprobación inicial.

### Verificación posterior: política de último estado e imagen del sentido

El 18 de octubre de 2026, cargando la clave desde `Backend/.env` solo en memoria,
el diagnóstico real respondió `traffic_density: 1.0` (27/27 km/h) y
`env_alert_active: 0`. La suspensión conservó su fecha del 13 de septiembre,
`notice_is_old: true` y `warning: old_notice_carried_forward`.
La geometría TomTom tuvo 94 puntos, distancia aproximada de 14 m al punto consultado
y orientación geométrica local de 123.1°. La coincidencia con el sentido real del
bus no se declara verificada por falta de una referencia georreferenciada de la foto.

Resultado actualizado: **75 pruebas de backend aprobadas**, diez de integración
omitidas sin emulador y esas **10 aprobadas por separado con Firestore Emulator**.
No se escribieron datos en Firebase remoto ni se reetiquetaron fixtures existentes.
