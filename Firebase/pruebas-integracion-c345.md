# Pruebas C.3–C.5: historial, feedback, alertas y reintentos

Esta guía permite probar la app sin un LSTM entrenado. **No sustituye el modelo**:
las consultas se precargan como fixtures y una predicción nueva sigue respondiendo
`503 MODEL_NOT_READY`. No se modifican reglas, índices ni el dataset de cámaras.

Estado: herramientas y pruebas locales preparadas; las diez fixtures del escenario
`manual-01` ya se cargaron y verificaron en Firebase de desarrollo. La API local
ya se inició. Las comprobaciones en el teléfono siguen pendientes.

### Proyecto confirmado y conexión verificada

- Proyecto de desarrollo confirmado por el usuario: `arribo-68326`.
- Base: `(default)`; UID de prueba: `0Qv94FxE5KZLJ0eRuB6WG4BV7Q93`.
- La configuración local de la app ya coincide con ese proyecto y usa modo `live`.
- La credencial externa indicada por el usuario permitió verificar la cuenta y
  crear cinco consultas y cinco alertas, sin sobrescribir documentos existentes.
  No se copió el JSON al repositorio ni a la app.
- La verificación remota de `manual-01` terminó con `failures: []`; todavía no hay
  feedback en sus cinco consultas, que quedan disponibles para probar en el teléfono.
- Se configuró el `.env` local de la app con
  `EXPO_PUBLIC_API_URL=http://192.168.100.240:8080`. La IP puede cambiar al cambiar
  de red; en ese caso actualiza el valor y recarga Expo.
- Se inició la API en el puerto `8080` y se verificó `/health` desde la dirección
  LAN: `status: ok`, `mode: production`, `model_ready: false`, `simulation: false`.
  Este último campo describe el modo de la API, no convierte las fixtures en datos
  reales: todas conservan `simulation: true`.
- No se publicaron reglas ni índices ni se modificó el dataset. Las pruebas desde
  el teléfono y el envío de reportes mediante una sesión real siguen pendientes.

Para continuar ahora: reinicia Expo desde `Codigo/App_Movil` con `npm start`,
conecta el teléfono a la misma red que la Mac e inicia sesión con la cuenta de
prueba. En Historial, abre `arribo-c345-manual-01-online` y reporta 15 minutos.
Después comprueba la recepción del reporte con el paso C.3. Las alertas de prueba
vencen cuatro horas después de la carga.

La API es un proceso local temporal, no un servicio instalado ni un despliegue.
Si se detiene, vuelve a iniciarla con el comando C.1 en una terminal con
`GOOGLE_APPLICATION_CREDENTIALS` configurada. Para detenerla, usa Ctrl+C en su
terminal. Si fue iniciada desde la sesión del asistente, solicita su detención
antes de volver a iniciar otra instancia en el mismo puerto.

## Antes de comenzar: dos entornos diferentes

### Actualización de los proveedores externos

Las nuevas consultas que obtengan contexto utilizan la política CAMe de último
estado reconocido: suspensión → `env_alert_active: 0`; activación/continuidad →
`1`, aunque el aviso sea antiguo. Su fecha y advertencia de antigüedad se conservan
en `context_sources`. Errores de red o interpretación siguen siendo desconocidos.
TomTom incorpora un resumen geométrico; no certifica todavía el sentido del bus.

Las pruebas automáticas de C.3–C.5 se ampliaron para comprobar que los estados
ambientales antiguos 0 y 1 y la procedencia TomTom pasan de la consulta al feedback,
conservan `actual_min` en historial y no cambian ni se duplican al reenviar.
`npm run test:integration` ejecuta ahora diez casos contra el emulador.

Esto **no actualiza retroactivamente** `manual-01`, sus reportes o sus alertas:
las fixtures conservan el contexto inventado de la precarga, no consultan los
proveedores al abrirlas ni al reportarlas. Para verificar las fuentes en vivo sin
LSTM usa `Backend/check_context.py` según
[contexto-real.md](../Backend/contexto-real.md). `/predict` sigue respondiendo
`MODEL_NOT_READY` sin modelo. `env_alert_active` tampoco publica documentos en
`alerts`; sus cinco avisos de prueba mantienen su vencimiento independiente.

- **Pruebas automáticas:** Firestore Emulator, proyecto ficticio `demo-arribo`.
  El backend utiliza el adaptador real de Firestore; la verificación del token se
  simula solo dentro de las pruebas. No acceden a tu Firebase remoto.
- **Prueba manual:** Firebase de desarrollo, cuenta real indicada abajo, app en
  modo `live` y backend local con Firebase Auth y Firestore. No usar el modo `demo`
  de la app para verificar escrituras remotas.

El SDK Admin del backend se autoriza mediante IAM y no mediante reglas de cliente.
Eso no implica abrir permisos: el backend sigue validando el token y obteniendo el
UID de la sesión; la app sigue sin poder escribir directamente historial o feedback.

## Paso A. Preparar la cuenta y el acceso de desarrollo

1. Usa el proyecto de desarrollo confirmado `arribo-68326` y la base `(default)`.
2. En Firebase Console → Authentication, verifica que exista y esté habilitada la
   cuenta `0Qv94FxE5KZLJ0eRuB6WG4BV7Q93`. No vuelvas a crearla.
3. Comprueba que el `.env` local de `App_Movil` apunta a ese mismo proyecto. No
   publiques credenciales ni tokens, ni los pegues en capturas o conversaciones.
4. Prepara credenciales ADC para el SDK Admin **fuera del repositorio**, con acceso
   autorizado al proyecto de desarrollo. `firebase login` autentica la CLI, pero no
   configura por sí mismo ADC para Python.

   Si ya utilizas una credencial de cuenta de servicio autorizada, configura
   `GOOGLE_APPLICATION_CREDENTIALS` con la ruta absoluta de su JSON externo. No
   copies el archivo a `Codigo`, no lo subas a Git y nunca lo uses como variable
   `EXPO_PUBLIC_*`. Prefiere credenciales administradas o impersonación cuando tu
   entorno ya las tenga configuradas; no es necesario crear otra clave por defecto.

   Para continuar sin Google Cloud CLI, si no tienes una credencial autorizada,
   abre Firebase Console → Configuración del proyecto → Cuentas de servicio →
   Firebase Admin SDK. Un administrador autorizado puede generar una clave privada
   de la cuenta de servicio para este entorno local, si la política del proyecto
   lo permite. No generes otra si ya cuentas con una credencial adecuada. Una clave
   privada es sensible: guárdala en un directorio privado **fuera del repositorio**
   y no compartas su contenido en conversaciones ni capturas.

   En la terminal que usarás para precargar datos e iniciar el backend, configura
   la ruta real de ese archivo externo:

   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS="/ruta/privada/fuera-del-repositorio/credencial-firebase.json"
   ```

   Usa la misma variable en cada terminal del backend. No es necesario modificar
   archivos de la app ni sus reglas. El entorno de producción deberá autorizar su
   propia identidad de servicio, sin incluir este JSON en la app ni en la imagen.
   Referencia: [configuración oficial de Firebase Admin](https://firebase.google.com/docs/admin/setup).
5. No configures `FIRESTORE_EMULATOR_HOST`, `FIREBASE_AUTH_EMULATOR_HOST` ni
   `ARRIBO_MODEL_DIR` en las terminales de la prueba manual. Los scripts rechazan
   mezclar estos entornos. Las pruebas automáticas configuran su propio emulador.

Los comandos siguientes apuntan explícitamente a `arribo-68326`, confirmado como
proyecto de desarrollo. Nunca los utilices contra producción: las alertas
son visibles para todos los usuarios autenticados de ese proyecto de desarrollo.

## Paso B. Precargar las consultas simuladas

Desde `Codigo/Backend`, utiliza el entorno Python existente. Si todavía no está
preparado, crea un entorno e instala las dependencias (solo en ese caso):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

No necesitas `requirements-model.txt` ni TensorFlow para esta prueba sin modelo.

### B.1. Inspeccionar sin conectar a Firebase

```bash
.venv/bin/python dev_fixtures.py preview --run-id manual-01
```

Debe mostrar `dry_run: true`, el UID indicado y diez documentos: cinco consultas
y cinco alertas. No requiere credenciales ni escribe archivos de respaldo.

### B.2. Autorizar la carga en desarrollo

```bash
.venv/bin/python dev_fixtures.py seed --run-id manual-01 --project arribo-68326 --confirm-dev-project arribo-68326 --apply
```

Sin `--apply`, `seed` solo muestra la vista previa. La confirmación del proyecto
es una protección frente a errores de selección: tú debes confirmar que es de
desarrollo; el script no puede deducirlo por el nombre del proyecto.

Se crean estos documentos bajo
`query_history/0Qv94FxE5KZLJ0eRuB6WG4BV7Q93/queries/`:

| Sufijo del ID | Uso manual sugerido |
| --- | --- |
| `arribo-c345-manual-01-online` | Reportar 15 minutos con conexión. |
| `arribo-c345-manual-01-offline` | Reportar 18 minutos sin conexión y reenviar. |
| `arribo-c345-manual-01-retry` | Observar carga y reintento tras fallo de red. |
| `arribo-c345-manual-01-long` | Reportar 60 minutos, fuera del dominio predictivo. |
| `arribo-c345-manual-01-expired` | Reportar sobre una consulta ya vencida. |

Todas tienen `station_id: rio_consulado`, estimación ficticia de 12 minutos,
intervalo `[8, 17]`, `source: mock`, `simulation: true` y versión
`test-fixture-c345`. Los datos climáticos también son inventados. Nunca deben
interpretarse como resultados del modelo, observaciones reales o parte del dataset.

El script no cambia `users/{uid}`, no crea cuentas y no sobrescribe documentos:
si repites el mismo escenario, conserva fechas, reportes y documentos existentes.
Si encuentra una colisión sin las marcas de esta prueba, cancela la escritura.
Para repetir desde cero, usa otro `--run-id`, por ejemplo `manual-02`; no borres
el historial del usuario ni reinicies documentos que ya tengan feedback.

Para probar paginación, puedes añadir `--extra-queries 20` a la carga: habrá 25
consultas de este escenario. Usa el mismo argumento en la verificación posterior.
La antigüedad y el volumen de historial existente pueden hacer que necesites
cargar más páginas para verlas.

## Paso C. Enviar feedback mediante el backend existente

### C.1. Iniciar la API de desarrollo, sin modelo

Desde `Codigo/Backend`:

```bash
.venv/bin/python run_development.py --project arribo-68326 --confirm-dev-project arribo-68326 --host 0.0.0.0 --port 8080
```

Este lanzador usa el modo interno `production` del backend porque ese es el nombre
del adaptador con Firebase real. **No despliega nada a producción**, no configura
un predictor falso ni habilita tokens demo. No establece `ARRIBO_MODEL_DIR`.

El puerto `8080` es el de la API; el `8082` queda reservado al emulador de pruebas.
Usa una red local de confianza; no expongas este servidor HTTP a Internet. Para
desarrollo remoto, utiliza un entorno protegido con HTTPS.

### C.2. Conectar la app

En el `.env` local existente de `Codigo/App_Movil`, conserva la configuración
Firebase y ajusta únicamente el modo y la URL de la API:

```dotenv
EXPO_PUBLIC_APP_MODE=live
EXPO_PUBLIC_API_URL=http://IP_LOCAL_DE_TU_MAC:8080
```

Sustituye la IP y reinicia Expo para recargar el entorno. El teléfono y la Mac deben
estar en la misma red y el firewall debe permitir la conexión. En un teléfono físico
`localhost` apunta al teléfono, **no** a la Mac. No agregues credenciales Admin a la
app. Inicia sesión con la cuenta de prueba.

Puedes abrir `http://IP_LOCAL_DE_TU_MAC:8080/health` para comprobar conectividad:
debe indicar `model_ready: false`. Eso es correcto en este escenario.

### C.3. Reportar una espera

1. Abre Historial y actualiza. Identifica una consulta de prueba por su ID y su
   indicación de simulación, no solo por los minutos.
2. Abre `arribo-c345-manual-01-online` y reporta **15 minutos**.
3. Espera la confirmación de recepción por el servidor. En modo live con estas
   fixtures, la interfaz debe indicar que es un **reporte simulado enviado**, no
   que fue guardado únicamente en una demostración local.
4. En Firebase Console, consulta:

   ```text
   feedback/0Qv94FxE5KZLJ0eRuB6WG4BV7Q93/entries/arribo-c345-manual-01-online
   query_history/0Qv94FxE5KZLJ0eRuB6WG4BV7Q93/queries/arribo-c345-manual-01-online
   ```

   El reporte debe tener `actual_min: 15`, `predicted_min: 12`, `error_min: 3`,
   `station_id: rio_consulado`, `simulation: true`, el UID correcto, el contexto y
   la fecha de la consulta original, y `received_at` generado por el servidor.
   El historial también debe tener `actual_min: 15`. Son escrituras transaccionales.
5. Desde Backend, verifica sin modificar datos:

   ```bash
   .venv/bin/python dev_fixtures.py verify --run-id manual-01 --project arribo-68326 --require-feedback online
   ```

   El resultado debe tener `failures: []`. Antes de reportar, la ausencia de
   feedback es normal si no usas `--require-feedback`.

### C.4. Comprobar la ausencia del modelo

En Predicción, selecciona Río Consulado–Misterios y pulsa **Obtener predicción**.
Una consulta nueva debe responder `503 MODEL_NOT_READY`; la app debe comunicar
indisponibilidad, no mostrar una estimación ficticia como real. No se genera una
consulta exitosa nueva mientras no exista un modelo listo.

Esto no impide reportar esperas para consultas precargadas. Una repetición con el
ID de una consulta ya existente puede devolver esa consulta por idempotencia; por
eso la prueba automática de `MODEL_NOT_READY` utiliza siempre un ID nuevo.

## Paso D. Comprobar alertas

1. Abre Alertas con conexión. Deben aparecer tres mensajes **PRUEBA C.3–C.5** del
   escenario, con severidades informativa, advertencia y crítica.
2. Verifica texto, icono y color, no solo el color. La alerta inactiva y la vencida
   de ese escenario no deben aparecer. Otras alertas legítimas pueden coexistir.
3. Las alertas activas vencen cuatro horas después de la carga. Repetir `seed` no
   renueva su vigencia; usa un escenario nuevo para una sesión posterior.
4. Al terminar, puedes desactivar **solo** las alertas de este escenario:

   ```bash
   .venv/bin/python dev_fixtures.py retire-alerts --run-id manual-01 --project arribo-68326 --confirm-dev-project arribo-68326 --apply
   ```

   No elimina documentos ni modifica alertas ajenas. Los reportes y consultas se
   conservan como evidencia de la prueba. Si no existen otras alertas vigentes,
   comprueba que la app muestre una lista vacía normal, no un error.

## Paso E. Estados de interfaz y asociación/idempotencia

Realiza estas comprobaciones y marca únicamente las que hayas observado en el
teléfono. Los resultados automáticos no equivalen a una verificación física.

- [ ] **Carga:** actualiza Historial y envía un reporte. Mientras la solicitud esté
  en curso, observa el indicador y evita envíos duplicados al pulsar varias veces.
  Una conexión rápida puede hacer que el estado dure muy poco.
- [ ] **Sin conexión:** carga previamente el historial y abre la consulta `offline`.
  Desactiva la conexión del teléfono y reporta 18 minutos. Debe quedar pendiente,
  conservando el ID y la estación originales, sin presentarse como recibido.
- [ ] **Persistencia de la cola:** cierra y vuelve a abrir la app con ese pendiente.
  Comprueba que no se pierda. Puede mostrarse historial almacenado localmente o un
  aviso de conexión: no interpretes los datos en caché como una lectura remota nueva.
- [ ] **Reenvío:** restablece Internet y mantén la API disponible. Usa el reintento
  de pendientes si es necesario. Actualiza Historial y verifica el mismo documento
  `feedback/{uid}/entries/{qid}` y `actual_min: 18` en su consulta; no otra consulta.
- [ ] **Backend no disponible:** con Internet activo, detén la API local y utiliza
  la consulta `retry`. El fallo de red puede tardar hasta el timeout. El reporte
  debe conservarse pendiente; vuelve a iniciar la API y reintenta. No debe aparecer
  una confirmación de recepción antes del éxito.
- [ ] **Alertas y red:** recuerda que Alertas e Historial leen Firestore directamente.
  Detener únicamente la API **no** prueba sus errores de lectura. Desconecta el
  teléfono para revisar avisos/reintentos; el SDK puede servir caché. Un error no
  debe presentarse como “no hay alertas” o “historial vacío”.
- [ ] **Listas vacías:** prueba Alertas tras retirar las fixtures, si no hay otras
  activas. Para Historial usa una cuenta de prueba sin consultas o verifica antes
  de precargar. No borres historial existente para forzar este estado.
- [ ] **Espera larga:** reporta 60 en `long`. Debe conservarse con
  `outside_domain: true`; no se descarta por superar 45 minutos.
- [ ] **Consulta vencida:** reporta una espera en `expired`. El vencimiento de la
  predicción no cambia el ID ni impide reportar una espera observada después.
- [ ] **Paginación:** si cargaste consultas adicionales, usa Cargar más y verifica
  que el historial no repita tarjetas ni pierda los reportes pendientes.
- [ ] **Aislamiento:** cierra sesión y, si dispones de otra cuenta de desarrollo,
  inicia sesión con ella. No debe ver el historial ni la cola de la primera cuenta.
  Las alertas del proyecto sí son compartidas entre usuarios autenticados.
- [ ] **Sin modelo:** confirma la indisponibilidad en Predicción sin impedir
  Historial, Feedback ni Alertas.

Verificación final de los dos reportes principales:

```bash
.venv/bin/python dev_fixtures.py verify --run-id manual-01 --project arribo-68326 --require-feedback online offline
```

La idempotencia de una solicitud idéntica y la concurrencia se verifican también
automáticamente: repetir el mismo `qid` y valor devuelve el mismo reporte; intentar
otro valor para un `qid` ya reportado devuelve `409`, sin sobrescribir el primero.
Si Firestore agota sus reintentos por contención o indisponibilidad temporal, el
backend devuelve `503 SERVICE_ERROR`, no `409 FEEDBACK_EXISTS`. Reintenta el mismo
valor e ID; un fallo temporal no confirma recepción. La prueba concurrente admite
ese resultado temporal y exige que el reenvío posterior tenga éxito sin duplicados.
La interfaz no debe habilitar edición de un reporte confirmado solo para facilitar
esta prueba. Errores HTTP de permisos o conflictos no equivalen a errores de red.

## Ejecutar las pruebas automáticas

Desde `Codigo/Backend`:

```bash
.venv/bin/python -m pytest -q
```

Sin emulador, las pruebas de integración se omiten explícitamente. Desde
`Codigo/Firebase`, con dependencias instaladas y Java disponible:

```bash
npm run test:rules
npm run test:integration
```

Ejecuta los comandos por separado: ambos utilizan el puerto `8082`. Las reglas
prueban permisos de cliente; la integración prueba el backend Admin y sus
transacciones. Son verificaciones complementarias, no intercambiables.

La integración utiliza el Python de `../Backend/.venv`, requiere las dependencias
de Backend y limita su conexión al emulador local. Crea IDs únicos y limpia solo
sus propios documentos del emulador, no la base real. Comprueba:

- Orden del historial y precarga sin sobrescrituras.
- Feedback asociado al UID, consulta, estación y contexto originales.
- Actualización transaccional de `actual_min` en historial.
- Repeticiones idénticas y concurrentes sin duplicados; conflictos sin sobrescribir.
- Rechazo de usuario ajeno, falta de autenticación y estación falsificada.
- `MODEL_NOT_READY` sin crear consultas nuevas.
- Esperas cero/largas, rangos inválidos y exclusión de feedback simulado del contexto.
- Alertas vigentes, inactivas, vencidas y retirada limitada al escenario.

Las fixtures se mantienen con `simulation: true`. El adaptador actual las excluye
del contexto reciente de inferencia; cualquier futuro trabajo de reentrenamiento
también deberá excluirlas explícitamente. No se exportan al dataset de cámaras.

El emulador no comprueba la necesidad real de índices compuestos. Si Firebase
remoto solicita un índice, revisa la consulta y `firestore.indexes.json`: no abras
reglas para evitar un error de índice. Esta preparación no publica reglas ni índices.

Referencia: [límites del emulador Firestore](https://firebase.google.com/docs/emulator-suite/connect_firestore).

## Resultado local de esta preparación — 17 de octubre de 2026

- Backend sin emulador: **28 aprobadas**, siete de integración omitidas por requerir
  el emulador; estas se ejecutan por separado con `test:integration`.
- Backend con Firestore Emulator: **7 pruebas de integración aprobadas**, incluido
  el reenvío idempotente tras contención temporal.
- Reglas de cliente: **60 aprobadas**; las reglas no fueron modificadas para esta tarea.
- TypeScript y lint de la app: aprobados.
- Pruebas de la app: **31 aprobadas y dos fallidas** por diferencias preexistentes
  entre la lista de requisitos/recomendaciones de contraseña y sus expectativas
  en `tests/auth.test.ts`. No se cambió esa política como parte de esta tarea.
- Firebase remoto: credencial validada, diez fixtures creadas y verificadas sin
  errores; API local y `/health` comprobados. No se enviaron reportes en nombre
  del usuario ni se marcaron las casillas manuales como completadas.
- Dispositivo, token real enviado desde la app, feedback online/offline, reintentos
  e índices exigidos por las consultas móviles: pendientes de comprobación manual.
