# ARRIBO — aplicación móvil

Expo SDK 58, TypeScript y Expo Router. Pantallas en `src/app`: acceso, Predicción, Historial, Alertas, Configuración y reporte de espera.

## Migración controlada de SDK 57 a SDK 58

La actualización se realizó en `migration/updating-to-sdk58`. Node `24.11.1` y npm
`11.6.2` son el entorno utilizado. SDK 58 requiere Node 22.13+ en la rama 22,
24.3+ en la rama 24, o una rama compatible posterior; conservar una versión par
compatible. Ver las [notas de SDK 58](https://expo.dev/changelog/sdk-58) y la
[guía de migración de Router](https://docs.expo.dev/router/migrate/sdk-57-to-58/).

Las versiones instaladas son Expo `58.0.19`, Expo Router `58.0.26`, React `19.3.0`
y React Native `0.88.0`. `package-lock.json` registra el árbol completo; usar
`npm ci` al instalarlo en otro entorno. Se alinearon los módulos con Expo, se
actualizaron las referencias de campos a `TextInputInstance`, los tipos de refs
del componente de pestañas web y el manejo de tema sin preferencia. Los iconos
conservan los colores de la paleta. Los plugins adicionales de `app.json` fueron
añadidos por `expo install --fix`.

Dos pruebas de contraseña ya fallaban antes de la actualización: esperaban una
longitud máxima en la lista visual y cinco recomendaciones. Se corrigieron sus
expectativas para respetar las opciones actuales del usuario (tres recomendaciones).
No se cambiaron `auth-validation.ts`, la política Firebase ni el proceso de registro.

El backend Docker, los contratos HTTP, las reglas de Firestore y los recursos de
GCP no necesitan desplegarse de nuevo por esta migración móvil. La comprobación
HTTPS de `https://arribo-api-jueg2ew3oq-uc.a.run.app` devuelve `/health` 200,
`/ready` 503 `MODEL_NOT_READY` y `/predict` sin sesión 401 `UNAUTHENTICATED`.
Son comprobaciones públicas: no acreditan aún el acceso autenticado desde el iPhone.

### Verificaciones automáticas

- `npx expo install --check`: dependencias compatibles con SDK 58.
- `npx expo-doctor`: 20/20 comprobaciones aprobadas.
- `npm run typecheck` y `npm run lint`: aprobados.
- `npm test`: 41/41 pruebas aprobadas.
- `npm ci --no-audit --no-fund`: instalación limpia desde el lockfile completada.
- `npx expo export --platform ios --platform android --max-workers 2 --output-dir /private/tmp/arribo-sdk58-native-export`:
  exportación terminada con bundles Hermes para ambas plataformas. No instala una
  app en el teléfono ni verifica las interacciones físicas.

La exportación adicional con `--platform all` se interrumpió al prolongarse la
fase web, después de compilar las plataformas móviles; macOS también reportó
temporalmente falta de recursos para crear un proceso durante esa comprobación.
No se da por aprobada la exportación web. Se completó por separado la exportación
móvil anterior, que corresponde al alcance de Expo Go en este proyecto.

`npm audit` detectó nueve entradas de severidad alta en dependencias transitivas
de Expo CLI y Firebase. Su corrección queda pendiente de una revisión específica:
no se aplicó `npm audit fix --force`, que propone versiones antiguas incompatibles
de Expo/Firebase. Que Expo Doctor y las pruebas pasen no equivale a una auditoría
de seguridad aprobada.

### Comprobación manual en iPhone (pendiente)

1. Detener el Metro anterior con `Ctrl+C`. En el `.env` local conservar las
   variables Firebase y verificar lo siguiente, sin subir ese archivo al repositorio:

   ```dotenv
   EXPO_PUBLIC_APP_MODE=live
   EXPO_PUBLIC_API_URL=https://arribo-api-jueg2ew3oq-uc.a.run.app
   ```

2. Desde `Codigo/App_Movil`, ejecutar `npx expo start --go --clear`, escanear el QR
   con el iPhone y abrirlo en Expo Go compatible con SDK 58. Metro sigue siendo
   local; FastAPI ya funciona en GCP y no requiere iniciar el servidor local.
3. Verificar acceso, Crear cuenta y Recuperar contraseña como rutas separadas.
   Si se prueba el registro, usar una cuenta de prueba propia y comprobar el nombre
   en Auth y Firestore. Un resultado parcial no debe crear otra cuenta al reintentar.
4. Iniciar sesión, recargar/cerrar y reabrir ARRIBO para comprobar persistencia.
   Cerrar sesión y confirmar que no se puede acceder a las pestañas protegidas.
5. Abrir las cuatro pestañas y el selector de estación. Comprobar búsqueda, cierre,
   teclado, modo oscuro y regreso del modal de feedback. En live solo se admite
   Río Consulado – Misterios para consultas reales.
6. Solicitar una predicción: debe aparecer la indisponibilidad del predictor, no un
   error de red. Sin LSTM aprobado no habrá una nueva estimación real ni un caso
   nuevo para probar miss/hit de una predicción real.
7. Revisar el historial propio y, si existe una consulta **simulada de prueba sin
   reporte**, enviar una espera desde ella. Confirmar el mismo `qid` en feedback y
   `actual_min` en historial. No reemplazar un reporte anterior ni usar consultas
   ficticias como datos reales. Si no hay ninguna pendiente, posponer este envío.
8. Revisar alertas: comprobar las activas no vencidas de la estación seleccionada,
   o el estado vacío cuando no existan. No habilitar push.
9. Activar ubicación opcional, probar permiso concedido/denegado y regresar a
   Predicción. El aviso de distancia usa 200 m y no debe bloquear una consulta.
10. En una consulta de prueba pendiente, comprobar carga, doble toque, desconexión,
    reporte en cola y reconexión sin duplicación. Confirmar también el aislamiento
    del historial y de la cola al cambiar entre cuentas de prueba propias.

Estas acciones físicas y las escrituras autenticadas siguen pendientes hasta
ejecutarlas en el dispositivo. Las pruebas unitarias y la exportación no las sustituyen.

## Probar sin modelo ni Firebase

```sh
npm ci
npx expo start --go
```

Abre la app con Expo Go compatible con SDK 58 y toca **Explorar demostración**. El modo predeterminado es `demo`; todos los resultados se identifican como ejemplos. Configuración permite simular demora, error y listas vacías. No se envía ningún dato a Firebase ni al backend en esta modalidad. Consultas, preferencias y reportes demo se guardan localmente.

## Conectar servicios reales

1. Crea un `.env` local y selecciona `EXPO_PUBLIC_APP_MODE=live`.
2. Configura Firebase público (apiKey, authDomain, projectId, appId); nunca pongas una cuenta de servicio en `EXPO_PUBLIC_*`.
3. Habilita Email/Password en Firebase Auth y Firestore. Publica reglas/índices de `../Firebase` después de probarlos.
4. Configura `EXPO_PUBLIC_API_URL`. Desde el teléfono, usa la IP LAN del servidor o HTTPS público, no `localhost`. Reinicia Expo después de cambiar variables.
5. El backend debe verificar tokens del mismo proyecto. Sin modelo aprobado, una consulta mostrará el error real de indisponibilidad.

GPS solicita permiso solo al activarlo; negarlo no bloquea las consultas. Historial y preferencias se aíslan por uid. Los reportes reales sin red se guardan en una cola y se reintentan al reconectar. No uses un backend `demo` con la app `live`: sus esquemas de autenticación son intencionalmente distintos.

### Recorrido de integración de la sección D

La app siempre consulta `/predict`; el servidor decide si reutiliza caché o ejecuta
el predictor y registra una consulta personal en ambos casos. La app no lee
`predictions` ni escribe historial o feedback directamente en Firestore.

El backend de desarrollo puede activar expresamente un predictor de integración
con Firebase Auth real: no es el backend `demo`, no es un LSTM y toda respuesta
queda marcada como simulada, incluso en cache hits. Guía:
[pruebas-integracion-d.md](../Firebase/pruebas-integracion-d.md).

Los reintentos conservan `request_id`, los dobles envíos comparten la consulta
activa y las respuestas de sesiones/estaciones anteriores se descartan. Se validan
respuesta, contexto e identidad antes de añadir historial. Un acuse de feedback
inválido o fallo transitorio conserva el reporte pendiente; restaurar sin conexión
mantiene su valor y no permite reemplazarlo por otro mientras está pendiente.

Pruebas específicas: `node --experimental-strip-types --test tests/query-flow.test.ts`.
La suite completa actual pasa las 41 pruebas; los requisitos de contraseña
existentes se conservaron durante la migración a SDK 58.

## Verificar

```sh
npm test
npm run lint
npm run typecheck
npx expo install --check
npx expo export --platform all
```

Expo genera los tipos de las rutas al ejecutar `expo start`. En una instalación limpia, inicia Expo antes de la primera comprobación de TypeScript.

## Builds

`expo-dev-client` y `eas.json` están preparados. Después de configurar la cuenta/proyecto y las variables de EAS:

```sh
npx eas-cli@latest build --profile development --platform android
```

`preview` y `production` usan servicios reales. Configura sus variables antes de construir. No se han generado builds firmados ni publicado la app. Push está deshabilitado hasta implementar el registro de tokens y el servicio de envío; Alertas sí funciona dentro de la app.

## Verificación manual pendiente

Dispositivo físico: acceso/registro/recuperación, permisos denegados, logout durante una petición, reconexión con reportes pendientes, aislamiento entre dos cuentas, fuentes grandes y modo oscuro. La exportación de bundles no sustituye estas pruebas. Revisar los avisos de seguridad detallados en `../README.md` antes de publicar.

## Rediseño de interfaz y autenticación

La revisión visual recupera la base del commit `c757c22`: una columna con ancho máximo de 640, encabezados simples, títulos de peso 700 y tarjetas de 20 px de padding. Se eliminó el panel azul promocional, se recuperó la navegación inferior sencilla y se mantuvieron las rutas independientes y las mejoras técnicas de autenticación. La paleta centralizada en `src/theme/palette.ts` combina superficies neutras y azul ARRIBO con acentos naranja y violeta e íconos semánticos. La inspiración son las [capturas públicas oficiales de Plata](https://bancoplata.mx/es/app); no se reutilizan sus imágenes, logotipos ni elementos bancarios.

Inicio de sesión (`src/app/(auth)/index.tsx`), creación de cuenta (`register.tsx`) y recuperación (`recover.tsx`) tienen rutas, formularios y estados independientes. Los componentes compartidos centralizan paleta clara/oscura, campos con etiquetas persistentes y mostrar/ocultar contraseña, tarjetas, botones, badges y mensajes. El diseño adapta el ancho, permite desplazamiento con teclado y habilita orientación horizontal; las transiciones respetan reducción de movimiento. No se añadieron dependencias.

El registro pide nombre para mostrar, correo, contraseña y confirmación. KnowledgeBase documenta el nombre del perfil; la confirmación es una mejora de UX y no se almacena. La contraseña se valida contra la política del proyecto mediante `validatePassword`, no con reglas de complejidad inventadas. El inicio de sesión solo exige campos válidos/no vacíos, sin bloquear contraseñas existentes por la política de nuevas cuentas.

El nombre se guarda en **Firebase Auth `displayName` y Firestore `users/{uid}.display_name`**. La validación compartida admite acentos y espacios interiores, elimina espacios exteriores y rechaza nombres vacíos o con saltos de línea. El límite es de 100 unidades UTF-16, igual al comportamiento verificado en las reglas: algunos emojis cuentan como dos unidades. El inicio de sesión no impone estas reglas a la contraseña existente.

`ensureProfile()` utiliza una transacción y el plan probado de `src/services/profile-plan.ts`: crea el perfil con nombre, o añade únicamente `display_name` a un perfil anterior que no lo tenga. No reescribe nombres existentes, `created_at` ni preferencias. Al iniciar sesión y reconectar se reintenta la sincronización; Configuración permite reintentar manualmente sin crear otra cuenta. El nombre verificado en Firestore se muestra en Configuración y se sincroniza con Auth.

Después de crear Auth se conserva temporalmente **solo el nombre** pendiente bajo `arribo:live:{uid}:pending-profile-name` en AsyncStorage, además de memoria, para recuperar fallos parciales. Se elimina al completar la sincronización; nunca contiene contraseñas ni confirmación. Si el dispositivo no permite guardar localmente y fallan ambos servicios, la recuperación del nombre solo está disponible en memoria hasta cerrar la app. Los reintentos están aislados por UID y no ejecutan `createUserWithEmailAndPassword`.

Una cuenta anterior sin nombre válido en Auth ni Firestore muestra **“Agrega un nombre válido”**. Configuración incluye un campo de nombre y **Guardar nombre** para completarlo, conservando el valor ante errores y bloqueando envíos duplicados. También se pueden reparar nombres antiguos vacíos o inválidos sin cambiar fecha ni preferencias; los nombres válidos existentes no se sobrescriben durante esta recuperación. Un fallo de red/permisos se diferencia del nombre faltante y se muestra con un aviso breve.

### Contraseñas y ubicación opcional

Crear cuenta distingue los **requisitos obligatorios de Firebase** de las **recomendaciones adicionales**: mayúscula, número y símbolo cuando todavía no son obligatorios. La lista visual conserva las opciones elegidas en el código actual; la validación completa sigue a cargo de `validatePassword` y Firebase. No se inventan requisitos remotos ni se cambia la política del proyecto desde la app. Para exigirlos, configurar Firebase Console → Authentication → Settings → Password policy, guardar la política y recargar por completo la app. No activar la actualización forzada al iniciar sesión si se desea conservar el acceso de cuentas anteriores. Las reglas de Firestore no controlan las contraseñas.

Ubicación opcional pide permiso en primer plano y guarda la preferencia. En Predicción se obtiene una posición puntual con precisión equilibrada y se compara con las coordenadas de la estación seleccionada. El aviso de lejanía significa una distancia calculada mayor de 200 metros; depende de la precisión del dispositivo y de las coordenadas configuradas. No bloquea la consulta ni incorpora coordenadas personales al modelo, historial o feedback. No hay seguimiento continuo/en segundo plano, ni se enciende o apaga el GPS del sistema con el interruptor.

**Publicación pendiente:** las reglas locales de `../Firebase/firestore.rules` admiten el campo, pero deben publicarse en el proyecto de desarrollo antes de probar las escrituras reales. Las pruebas locales no publican nada. Si las reglas remotas aún lo rechazan, la cuenta Auth permanece creada y se muestra el perfil pendiente. No se modifican índices ni contratos del backend. Ver `../Firebase/README.md` para el orden de publicación y comprobaciones.

La navegación espera a que terminen los pasos del registro. Si Auth crea la cuenta pero falla el nombre o el perfil de Firestore, se conserva la cuenta y se muestra un aviso de resultado parcial; no se vuelve a ejecutar la creación automáticamente. Los formularios y acciones de envío bloquean pulsaciones repetidas mientras están pendientes. El código no guarda contraseñas ni confirmaciones en AsyncStorage, Firestore o logs; la persistencia de autenticación sigue a cargo del SDK existente.

### Comprobaciones manuales del rediseño

- En `live`: navegar entre acceso, registro y recuperación; volver sin quedar atrapado en una ruta y comprobar que las rutas de cuenta no sean accesibles después de autenticar.
- Tras publicar las reglas de desarrollo: crear una cuenta de prueba, comprobar UID y `displayName` en Auth, `display_name` en Firestore y nombre visible en Configuración. Revisar los errores del perfil por separado. No recrear la cuenta si Auth ya tuvo éxito.
- Iniciar sesión con una cuenta anterior sin `display_name`, pero con nombre en Auth: debe completar solo ese campo sin cambiar fecha ni preferencias. Reconectar/reintentar y comprobar que un nombre existente no se sobrescribe ni se duplica el documento.
- Simular fallo de Firestore y recuperación: comprobar el aviso de cuenta creada, reinicio de app y botón de reintento en Configuración; verificar que no mezcla el nombre pendiente de otra cuenta. Estas pruebas con dispositivo/Firebase real siguen pendientes.
- En una cuenta sin nombre en ambos servicios, escribir un nombre válido en Configuración y guardarlo; comprobar Auth y Firestore. Rechazar vacío/multilínea/más de 100 unidades UTF-16; conservar el texto si falla la conexión.
- Probar el registro con política de seis caracteres y con una política más estricta configurada en Firebase: solo los requisitos efectivos son obligatorios; las recomendaciones no deben confundirse con ellos.
- Iniciar sesión con una cuenta existente, probar contraseña incorrecta, recuperar contraseña, cerrar sesión y reiniciar la app antes/después del cierre para verificar persistencia.
- Verificar errores por campo después de interacción, coincidencia exacta de contraseñas, mostrar/ocultar, foco mediante “Siguiente” y doble pulsación sin solicitudes duplicadas.
- En teléfonos pequeños, tablet y horizontal: abrir teclado, desplazarse al botón final y revisar fuentes grandes, lector de pantalla, modo oscuro y reducción de movimiento.
- En demo: explorar las pestañas, enviar feedback local y revisar escenarios normal/lento/error/vacío; confirmar que todos los ejemplos siguen identificados.
- En live: comprobar cargas, errores, caché local, avisos no disponibles, reconexión y reintentos. Las pruebas unitarias y exportación no verifican Firebase real ni sustituyen estas pruebas físicas.

### Archivos del rediseño

- Autenticación: `src/app/(auth)/index.tsx`, `register.tsx`, `recover.tsx` y `_layout.tsx`.
- Sistema visual: `src/components/arribo-ui.tsx`, `src/theme/palette.ts`, `src/hooks/use-reduced-motion.ts` y navegación en `src/app/_layout.tsx` y `src/app/(tabs)/_layout.tsx`.
- Pantallas: `src/app/(tabs)/index.tsx`, `history.tsx`, `alerts.tsx`, `settings.tsx` y `src/app/feedback.tsx`.
- Registro y validación: `src/state/arribo-provider.tsx`, `src/services/registration.ts`, `src/utils/auth-validation.ts` y `src/types/arribo.ts`.
- Verificación y configuración: `tests/auth.test.ts`, `tests/palette.test.ts`, `app.json` y este `README.md`. Las pruebas de paleta comprueban contraste de texto/badges/botones activos y bordes de campos; no sustituyen revisión visual, pruebas de teclado o lector de pantalla.

## Selector de estación en Predicción

La tarjeta de estación abre un `Modal` inferior con búsqueda por nombre/zona sin distinción de mayúsculas o acentos, lista desplazable, marca de selección y mensaje sin resultados. Botón de cierre, fondo y Atrás de Android utilizan el mismo cierre, que también limpia la búsqueda y descarta el teclado. Se mantienen colores azules, modo oscuro, áreas seguras, orientación horizontal y reducción de movimiento. No se añadieron dependencias ni mapa.

- `src/types/arribo.ts`: tipo `Station` y estación original en el feedback pendiente.
- `src/data/stations.ts`: catálogo local y disponibilidad por modo. Solo `rio_consulado` admite solicitudes reales, de acuerdo con `Backend/app/contracts.py`. `demo_norte` y `demo_centro` son ejemplos ficticios con coordenadas ilustrativas y quedan bloqueados en live; todos sus resultados son simulados.
- `src/components/station-selector.tsx` y `src/utils/station-picker.ts`: tarjeta, modal y estado de búsqueda/cierre.
- `src/screens/prediction-screen.tsx`: pantalla y comprobación de distancia para la estación seleccionada. `src/app/(tabs)/index.tsx` queda como ruta y reinicia la pantalla al cambiar de estación o usuario.
- `src/state/arribo-provider.tsx` y `src/utils/station-selection.ts`: selección compartida entre pestañas, favorita válida como valor inicial, protección frente a preferencias cargadas tarde y respuestas obsoletas, incluso A → B → A. Elegir una estación no modifica `preferred_station`. La selección actual no se persiste entre reinicios: al abrir una sesión se parte de la favorita válida.
- `src/services/demo.ts` y `src/services/api.ts`: resultados demo con la estación elegida y validación del `station_id` recibido. La solicitud real de `/predict` envía el ID seleccionado.
- Historial y feedback muestran la estación de la consulta original. La cola conserva `station_id`, pero `/feedback` continúa recibiendo únicamente `query_id` y `actual_min`: el backend recupera la estación del historial original. No se modificó su contrato ni las reglas de Firestore. Se conservan los reportes anteriores al cambio de estación y se recupera la estación de colas locales antiguas.
- Alertas filtra/consulta la estación seleccionada; Configuración sigue mostrando la favorita, no la selección temporal.
- `tests/stations.test.ts`: catálogo, permisos por modo, búsqueda, favorita, cambios de selección, cierre/reapertura, respuestas tardías y contrato de feedback.

### Prueba manual pendiente del selector

1. En demo, abrir la tarjeta, buscar `RIO`, `MISTERIOS`, `ZÓNA NÓRTE` y un texto inexistente; comprobar resultados, marca y mensaje vacío.
2. Cerrar con X, fondo y Atrás de Android; reabrir y comprobar búsqueda limpia. Revisar teclado, desplazamiento, lector de pantalla, modo oscuro, horizontal y fuentes grandes en dispositivo físico.
3. Seleccionar Estación Norte, consultar y cambiar de pestaña: la selección debe mantenerse y Configuración debe conservar la favorita original.
4. Con el escenario lento, iniciar una consulta y cambiar de estación antes de que responda, también A → B → A. El resultado o error anterior no debe aparecer en la nueva selección. Consultar después la nueva estación.
5. Desde Historial, reportar una consulta anterior: su estación no debe cambiar. En live, comprobar envío/reintento contra el backend y revisar la estación derivada de la consulta original.
6. En live, comprobar que solo Río Consulado – Misterios sea seleccionable. Para añadir estaciones reales habrá que ampliar catálogo, contrato del backend y fuentes/modelo por estación; cambiar un flag local no basta. El mapa sigue pendiente.

Las pruebas unitarias y la exportación verifican lógica/compilación; no prueban estos gestos ni servicios reales en un teléfono.
