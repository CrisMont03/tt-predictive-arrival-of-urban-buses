# Sección D: consulta, caché del servidor, historial y feedback

Esta guía prueba la integración sin entrenar ni sustituir silenciosamente el LSTM.
La app siempre llama a FastAPI. Un hit evita inferencia, no la llamada HTTP.
La app no lee `predictions` ni escribe historial o feedback directamente.

## 1. Pruebas automáticas aisladas

Desde `Codigo/Backend`:

```sh
.venv/bin/python -m pytest -q
```

Desde `Codigo/Firebase`:

```sh
npm run test:integration
npm run test:rules
```

Desde `Codigo/App_Movil`:

```sh
npm run typecheck
npm run lint
node --experimental-strip-types --test tests/query-flow.test.ts
npm test
```

El emulador usa `demo-arribo` en 8082. Los tests de integración usan el SDK y las
transacciones reales contra ese emulador; la verificación de tokens se sustituye
por identidades de prueba. Los tests de reglas usan clientes autenticados simulados.
No se escriben fixtures en el proyecto real ni se publican reglas.

La suite móvil completa contiene dos discrepancias preexistentes en `auth.test.ts`
sobre requisitos/recomendaciones de contraseña eliminados del código. No se
modificó esa política en D. Los tests nuevos de consulta se ejecutan por separado.

## 2. Preparar la prueba manual con Firebase de desarrollo

Usar `arribo-68326`, la base `(default)` y una cuenta de prueba existente.
La app debe estar en modo `live`, con la configuración Firebase ya utilizada en C.
No usar credenciales de servicio en Expo ni exponerlas en `EXPO_PUBLIC_*`.

Desde `Codigo/Backend`, en una terminal propia:

```sh
export GOOGLE_APPLICATION_CREDENTIALS="/ruta/externa/a/arribo-dev.json"
.venv/bin/python run_development.py \
  --project arribo-68326 \
  --confirm-dev-project arribo-68326 \
  --predictor integration \
  --context fixture \
  --host 0.0.0.0 \
  --port 8080
```

Sustituir la ruta por la credencial externa autorizada que ya se usa en desarrollo.
`ARRIBO_MODEL_DIR` debe estar sin configurar. No mezclar esta ejecución con variables
de emuladores. `--predictor integration` es una opción explícita: no se activa por
faltar el modelo. El runner la restringe al proyecto confirmado y rechaza Cloud Run.

`--context fixture` evita llamadas externas y claves adicionales. `--context real`
permite probar los proveedores existentes, pero el resultado sigue siendo simulado.
En ese caso el contexto puede cambiar y provocar misses: no se garantiza un hit
solo porque no hayan transcurrido cinco minutos.

La app conserva Metro en 8081; FastAPI usa 8080. Su `EXPO_PUBLIC_API_URL` debe
apuntar a `http://IP_LAN_DEL_EQUIPO:8080`, accesible desde el teléfono. No usar
`localhost` en el teléfono para referirse al equipo. Reiniciar Metro si cambia
una variable `EXPO_PUBLIC_*`.

Estos comandos son instrucciones manuales: no se inició un servidor remoto ni se
cargaron datos al proyecto real durante la implementación.

## 3. Verificar qué predictor está activo

Abrir `/health` desde el equipo o teléfono. Con integración activa se espera:

```json
{
  "status": "ok",
  "mode": "production",
  "model_ready": false,
  "predictor_ready": true,
  "predictor_kind": "integration",
  "simulation": true
}
```

`mode=production` selecciona los adaptadores Firebase y verifica tokens reales;
no significa que exista un LSTM desplegado. `model_ready=false` es correcto.
El predictor de integración consume la preparación v2 sin TensorFlow y produce
números deterministas de prueba. No son métricas ni resultados del futuro modelo.

## 4. Probar miss, hit e historial

1. Iniciar sesión y consultar Río Consulado–Misterios.
2. Comprobar aviso de demostración y versión `integration-v2-not-an-lstm`.
3. Si no había caché compatible, la fuente será `Demostración` (`source=mock`).
   Si quedó una caché vigente de una prueba anterior, ya puede ser `Caché`.
4. Volver a consultar en pocos segundos, con contexto fixture. La fuente debe ser
   `Caché` si siguen coincidiendo las entradas; el resultado continúa simulado.
5. Abrir Historial: cada consulta intencional tiene un registro distinto. Las
   consultas simuladas live muestran `Prueba: <query_id>` para poder identificarlas.
6. En Firestore, revisar `query_history/{uid}/queries/{qid}`: mismo snapshot de
   generación en un hit, pero `query_id` y `requested_at` nuevos. Comparar `timestamp`,
   `expires_at`, `context`, `model_version`, `source` y `simulation`.

Una caché vigente se comparte entre usuarios compatibles, pero cada historial es
privado. El contador del predictor en los tests demuestra que el hit no ejecuta
otra inferencia; la prueba manual no mide latencias del LSTM.

## 5. Probar feedback y pendientes

1. Abrir una consulta anterior del Historial y reportar, por ejemplo, 15 minutos.
2. Confirmar `feedback/{uid}/entries/{qid}` y `actual_min=15` en su historial.
3. Verificar `simulation=true`, estación y contexto original. `query_at` debe
   corresponder a `requested_at` de esa consulta, no a la generación de la caché.
4. Para otra consulta, apagar FastAPI con Ctrl+C e intentar enviar feedback.
   Debe quedar pendiente, no confirmado como enviado. También puede probarse
   desconectando el teléfono; la consulta debe haberse generado previamente.
5. Reabrir la app sin conexión: el reporte debe conservar valor y estado pendiente.
6. Restablecer conexión y servidor, y utilizar `Reintentar envío` en Configuración.
   Comprobar que queda un único documento de feedback con el mismo qid.

Un fallo transitorio o un acuse perdido conserva el reporte para reintentar.
No se permite reemplazar un valor pendiente por otro distinto. El servidor
acepta reintentos iguales y devuelve 409 si ya existe un reporte diferente.
Los tests cubren pérdida de respuesta y fallos parciales sin duplicar registros.

## 6. Vencimiento y ausencia de modelo

- Tras vencer la predicción, debe mostrarse como vencida; una consulta nueva
  obtiene un resultado nuevo. No es necesario esperar a que TTL borre el documento.
- La expiración máxima es cinco minutos desde la generación, y puede ser menor
  si las fuentes contextuales vencen antes. Un hit no renueva la expiración.
- Un reintento con el mismo `request_id` recupera la consulta original, incluso
  vencida: no debe confundirse con iniciar una consulta nueva.
- Detener el runner y volver a iniciarlo sin `--predictor integration` ni
  `ARRIBO_MODEL_DIR`. `/health` indicará predictor no disponible; una consulta
  nueva devolverá `503 MODEL_NOT_READY`. No se inventa un resultado como respaldo.

## 7. Mecanismo A: qué demuestra esta prueba

Los reportes de estas consultas simuladas se excluyen del Mecanismo A operativo y
del entrenamiento. Enviar 15 minutos aquí **no debe** convertir el dato en una
observación real ni obligar al predictor de prueba a adaptarse.

La selección causal se prueba con observaciones controladas en memoria aislada,
sin cargarlas como datos reales al proyecto de desarrollo. Se comprueba disponibilidad
por `query_at` y `received_at`, horizonte, estación, dominio, ausencia de simulaciones,
N−1 reportes, respaldos y cambio de caché cuando cambia una observación seleccionada.
Los tests de B cubren además longitudes 10/20/30 y equivalencia offline/online.

Conectar el LSTM aprobado después permitirá evaluar precisión y utilidad del feedback.
Estas pruebas no demuestran MAE <5 ni mejoras de predicción.

## 8. Seguridad y alcance

No se cambiaron reglas ni índices. Caché, historial y feedback conservan escritura
solo del backend; no hay lectura móvil de `predictions`. No se registran contraseñas,
tokens ni claves en esta guía. Las fixtures no se presentan como datos entrenables.
No se borran consultas anteriores ni se publica automáticamente una configuración.
