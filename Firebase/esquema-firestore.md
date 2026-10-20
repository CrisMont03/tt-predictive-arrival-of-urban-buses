# Esquema de Firestore — ARRIBO

Este documento explica qué guarda ARRIBO en su base `(default)`. No crea registros
ni publica reglas. Los nombres se conservan compatibles con la app y el backend.
Los permisos descritos corresponden al archivo local de reglas; no se presupone
que coincidan con las reglas que estén publicadas en Firebase Console.

- **Colección:** grupo de documentos, por ejemplo `users`.
- **Documento:** registro individual, por ejemplo el perfil de una persona.
- **Campo:** dato de ese registro, como `email`.
- **`uid`:** identificador de la cuenta generado por Firebase Auth.
- **`qid`:** identificador de una consulta, igual a `query_id`.
- **`timestamp`:** fecha y hora; **`map`:** grupo de campos anidados;
  **`boolean`:** `true` o `false`; **`number`:** número.

Los valores entre llaves en las rutas se sustituyen por identificadores reales.
Las colecciones se crean con su primera escritura; no hay que crearlas vacías.

## 1. `users/{uid}` — Perfil del usuario

**Para qué sirve:** guardar el nombre, correo y preferencias de cada persona.
El documento utiliza el UID de Auth como identificador; no necesita otro campo
`uid` dentro. `preferences` es un mapa dentro del perfil, no una subcolección.

| Campo | Tipo en Firestore | Origen y comportamiento |
|---|---|---|
| `email` | string | Correo de Firebase Auth. |
| `display_name` | string | Nombre introducido en el registro, sin espacios exteriores. |
| `created_at` | timestamp | Generado por el servidor al crear el perfil; no cambia después. |
| `preferences` | map | Agrupa las preferencias siguientes. |
| `preferences.preferred_station` | string | Inicialmente `"rio_consulado"`. |
| `preferences.gps_enabled` | boolean | Inicialmente `false`. |
| `preferences.notifications_enabled` | boolean | Inicialmente `false`. |

### La estructura esperada es:

```
users/{uid}
├── email: "ana@example.com"
├── display_name: "Ana María"
├── created_at: timestamp del servidor
└── preferences: map
    ├── preferred_station: "rio_consulado"
    ├── gps_enabled: false
    └── notifications_enabled: false
```

### Reglas de contenido del nombre

- Se permiten acentos y espacios interiores: `María José`.
- Se eliminan espacios al inicio y al final.
- No se acepta un nombre vacío o compuesto únicamente por espacios.
- Proponemos un máximo de 100 caracteres y no admitir saltos de línea.
- No se exige nombre legal ni apellidos separados.

Las reglas locales ya exigen de 1 a 100 caracteres, sin espacios exteriores ni
saltos de línea. La app comparte esa validación y envía el nombre recortado.
Las reglas rechazan valores inválidos; no los transforman.

### Qué funciona y qué falta

- La app crea el perfil con `ensureProfile()`; Firebase Auth no crea por sí solo
  el documento de Firestore. El código ahora guarda correo, nombre, fecha y preferencias.
- El nombre ya se guarda en **Auth** como `displayName`.
- Las reglas locales permiten **`display_name` en Firestore** y la app integra su
  escritura y reintento. Aún falta publicar las reglas y comprobar el flujo en Firebase
  real; pasar pruebas locales no publica las reglas ni actualiza usuarios reales.
- Se conservará el nombre en Auth. Guardarlo en ambos servicios requiere dos
  operaciones: una puede completarse aunque la otra quede pendiente.
- Para cuentas anteriores, se completa el nombre faltante desde Auth si
  está disponible, sin cambiar la fecha ni las preferencias ni sobrescribir un
  nombre válido existente. Si falta también en Auth, Configuración permite ingresarlo
  con el campo de nombre y el botón Guardar nombre. Los nombres anteriores vacíos
  o inválidos pueden repararse mediante ese mismo flujo.
- La transición debe admitir perfiles antiguos sin nombre; no se debe bloquear
  su cambio de preferencias por no haber completado la migración.
- Las reglas locales permiten crear y actualizar perfiles sin nombre durante esa
  transición. Una vez guardado un nombre válido, no permiten eliminar el campo;
  sí permiten sustituirlo por otro nombre válido.

**Quién accede actualmente:** cada usuario autenticado puede leer y crear/actualizar
su propio perfil validado, no el de otros. El cliente no puede eliminar el perfil.

No se guardan contraseñas, confirmaciones, teléfono, domicilio ni otros datos
personales innecesarios. El token FCM se incorporará cuando se implemente esa función.

## 2. `query_history/{uid}/queries/{qid}` — Historial de consultas

**Para qué sirve:** recordar qué predicción consultó cada usuario y a qué estación
correspondía. Cada consulta tiene su propio documento, incluso si utiliza caché.

Ejemplo de ruta ilustrativa: `query_history/usuario_123/queries/consulta_001`.

| Campo | Tipo en Firestore | Explicación |
| --- | --- | --- |
| `query_id` | string | Identificador de la consulta, igual al ID del documento. |
| `station_id` | string | Estación de la consulta original. |
| `waiting_time_min` | number | Minutos estimados de espera. |
| `confidence_interval` | array de dos números | Límite inferior y superior de la estimación. |
| `model_version` | string | Versión o identificador del predictor utilizado. |
| `timestamp` | timestamp | Cuándo se generó la predicción. |
| `requested_at` | timestamp | Cuándo hizo la consulta el usuario; se usa para ordenar el historial. |
| `expires_at` | timestamp | Cuándo deja de estar vigente la predicción. |
| `source` | string | `mock` (simulación), `cache` o `model`. |
| `simulation` | boolean | Indica si los datos son simulados. |
| `context` | map | Condiciones de la consulta, descritas al final del documento. |
| `actual_min` | number, opcional | Espera real; se añade cuando el backend recibe el feedback. |

**Ejemplo sencillo:** el usuario consultó una estimación de 12 minutos y después
reportó que esperó 15. El historial conserva la estimación y añade la espera real.
Estos números son ilustrativos, no una predicción real del sistema.

**Quién escribe:** el backend. **Quién lee desde la app:** solo el propietario.
`feedback_pending` indica un envío pendiente en el dispositivo; no es un campo
que el backend guarde en este documento. Cambiar de estación no cambia la estación
de consultas anteriores ni elimina su posibilidad de reportar la espera.

## 3. `feedback/{uid}/entries/{qid}` — Espera reportada

**Para qué sirve:** guardar cuánto esperó realmente el usuario y vincularlo a la
predicción que recibió. Se utiliza el mismo `qid` del historial, no un ID nuevo
para cada intento de envío.

Ejemplo relacionado con el anterior: `feedback/usuario_123/entries/consulta_001`.

| Campo | Tipo en Firestore | Explicación |
| --- | --- | --- |
| `query_id` | string | Consulta original; coincide con el ID del documento. |
| `uid` | string | Usuario propietario del reporte. |
| `station_id` | string | Estación de la consulta original. |
| `actual_min` | number | Minutos que el usuario reporta haber esperado. |
| `predicted_min` | number | Minutos estimados en la consulta original. |
| `error_min` | number | `actual_min - predicted_min`. |
| `context` | map | Condiciones de la consulta original, no las del envío del reporte. |
| `query_at` | timestamp | Cuándo se hizo la consulta. |
| `received_at` | timestamp | Cuándo recibió el backend el reporte. |
| `simulation` | boolean | Indica si proviene de una consulta simulada. |
| `outside_domain` | boolean | Actualmente indica una espera mayor de 45 minutos. |
| `model_version` | string | Versión utilizada para la estimación original. |

Con el ejemplo anterior: `predicted_min = 12`, `actual_min = 15` y `error_min = 3`.
El contrato actual permite reportar entre 0 y 180 minutos. Marcar `outside_domain`
no borra el reporte; el backend lo excluye de sus observaciones recientes de inferencia.

**Cómo se guarda:** la app envía `query_id` y `actual_min` a `/feedback`. El backend
verifica la consulta del usuario y obtiene su estación y demás datos originales.
Después guarda el reporte y actualiza el historial mediante una transacción.

Si se reenvía el mismo valor para la misma consulta, se devuelve el reporte existente.
Si se intenta reemplazarlo por otro valor, el backend devuelve un conflicto.

**Permisos actuales:** el usuario puede leer sus reportes, pero la app no escribe
directamente en esta colección. Los datos simulados no deben usarse para entrenar;
guardar feedback no significa que el reentrenamiento ya esté desplegado.

## 4. `alerts/{alertId}` — Avisos por estación

**Para qué sirve:** mostrar avisos del servicio o contingencias asociados a una
estación. La app obtiene el identificador del documento; no hace falta otro campo `id`.

| Campo | Tipo en Firestore | Explicación |
| --- | --- | --- |
| `station_id` | string | Estación a la que corresponde el aviso. |
| `type` | string | Categoría del aviso. |
| `message` | string | Texto mostrado al usuario. |
| `severity` | string | `info`, `warning` o `critical`. |
| `active` | boolean | Indica si la alerta está activa. |
| `created_at` | timestamp | Fecha de publicación. |
| `expires_at` | timestamp | Fecha en la que vence. |

**Ejemplo ilustrativo:** una advertencia de retrasos en `rio_consulado`, con
`severity = warning` y `active = true`.

La app consulta por estación y `active == true`. El listener actual no filtra
por `expires_at`; debe revisarse el tratamiento de alertas vencidas y mantenerse
coherente el estado activo. No se da por implementada su publicación automática ni FCM.

Se conservan los nombres `type`, `message` y `severity`, utilizados por el código,
aunque algunos ejemplos de KnowledgeBase los escriben en español.

**Quién lee:** usuarios autenticados. **Quién publica:** sistema o administración
autorizada, no los usuarios desde la app.

## 5. `predictions/{cache_key}` — Predicciones reutilizables

**Para qué sirve:** conservar temporalmente una predicción para reutilizarla cuando
el backend determine que corresponde al mismo contexto, sin volver a ejecutar el modelo.

`cache_key` es el ID calculado por el backend mediante un hash que considera estación,
fecha/hora local, versión y revisión del contexto/observaciones. No se crea a mano
ni necesita duplicarse como campo del documento.

| Campo | Tipo en Firestore | Explicación |
| --- | --- | --- |
| `query_id` | string | Consulta asociada a la generación del registro de caché. |
| `station_id` | string | Estación de la predicción. |
| `waiting_time_min` | number | Minutos estimados. |
| `confidence_interval` | array de dos números | Límites de la estimación. |
| `model_version` | string | Versión del predictor. |
| `timestamp` | timestamp | Momento de generación. |
| `requested_at` | timestamp | Momento de la consulta guardada al crear la caché. |
| `expires_at` | timestamp | Vencimiento comprobado por el backend. |
| `expiresAt` | timestamp | Mismo vencimiento, utilizado por la configuración TTL. |
| `source` | string | Origen de la predicción guardada. |
| `simulation` | boolean | Distingue datos simulados de reales. |
| `context` | map | Condiciones utilizadas para la consulta. |

La vigencia actual es de cinco minutos. Al reutilizar la caché, el backend registra
en el historial el identificador y la fecha de la consulta nueva; no debe confundirse
con el `query_id` conservado en la caché.

**No renombrar `expires_at` ni `expiresAt`:** ambos tienen un uso en el código actual.
TTL es la limpieza automática configurada para registros vencidos; no garantiza
su eliminación inmediata ni sustituye comprobar su vigencia antes de reutilizarlos.
La configuración local no significa que esa política ya esté publicada.

**Permisos actuales:** el cliente no puede leer ni escribir esta colección; accede
el backend autorizado. La lectura directa desde la app prevista en el plan sigue
pendiente de una integración y revisión de seguridad específicas.

### Contexto compartido: campo `context`

Historial, feedback y caché guardan este mapa:

| Campo | Tipo | Significado |
| --- | --- | --- |
| `hour` | integer | Hora local, de 0 a 23. |
| `day_of_week` | integer | Día, de 0 (lunes) a 6 (domingo). |
| `is_weekend` | integer | 0 o 1. |
| `is_holiday` | integer o null | 0 o 1 si se conoce el dato. |
| `precipitation_mm` | number o null | Precipitación en milímetros. |
| `temp_c` | number o null | Temperatura en grados Celsius. |
| `humidity` | number o null | Humedad relativa. |
| `traffic_density` | number o null | Densidad de tráfico. |
| `env_alert_active` | integer o null | Indicador de contingencia ambiental. |

Cuando el contrato admite un dato desconocido, conservar `null` en lugar de inventar
un cero. Guardar estas variables no significa que todas sean entradas del LSTM.
Este documento no modifica el contrato del modelo.

### Integración con el repositorio y estado del trabajo

- `App_Movil/src/types/arribo.ts`: tipos consumidos por la app.
- `App_Movil/src/state/arribo-provider.tsx`: perfil, preferencias, historial y alertas.
- `Backend/app/contracts.py`: solicitudes y respuestas del servicio.
- `Backend/app/main.py`: construcción de predicciones y reportes.
- `Backend/app/store.py`: escrituras, transacciones y caché en Firestore.
- `Firebase/firestore.rules`: reglas locales con validación de `display_name`, sin publicar.
- `Firebase/firestore.indexes.json`: índices y TTL existentes, sin cambios en este paso.

Las pruebas locales cubren nombres válidos e inválidos, perfiles anteriores sin
nombre, permisos entre usuarios, campos obligatorios e inmutables, preferencias y
acceso de clientes a las cinco colecciones. No implementan la migración de la app
ni validan exhaustivamente los contratos del backend o los índices compuestos.

La escritura, validación compartida y reintento ya están integrados en la app.
Antes de usar esa escritura contra Firebase real deben publicarse las reglas
probadas en el proyecto de desarrollo correcto. Después falta comprobar el flujo
en dispositivo y la base real. No se crean datos reales ni se publican cambios por
documentar este esquema o ejecutar el emulador.
