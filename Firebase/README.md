# Firebase — esquema y pruebas locales

Paquete independiente de `App_Movil`. No requiere cambiar dependencias de Expo,
iniciar sesión en Firebase CLI, descargar credenciales ni conectar la app al emulador.

## Archivos

- `esquema-firestore.md`: contrato de las cinco colecciones, estado actual y cambios pendientes.
- `firestore.rules`: reglas locales con soporte para `display_name` y perfiles antiguos.
- `firestore.indexes.json`: índices y TTL existentes, sin publicar automáticamente.
- `firebase.json`: configuración existente, con Firestore local en el puerto `8082`.
- `tests/firestore.setup.test.mjs`: pruebas de nombres, perfiles antiguos, validación y permisos de las cinco colecciones.

## 1. Requisitos

Usar Node.js 24 LTS (npm incluido) y Java 21 o posterior. Verificar con:

```bash
node --version
npm --version
java -version
```

## 2. Instalar las herramientas

Desde `Codigo/Firebase`:

```bash
npm ci
```

`package-lock.json` fija las versiones. Conservarlo en Git junto con `package.json`;
no versionar `node_modules`. No ejecutar `firebase init`: la configuración ya existe.

## 3. Verificar el entorno de pruebas

```bash
npm run test:rules
```

El comando inicia exclusivamente el emulador Firestore, ejecuta las pruebas y lo
detiene. La primera ejecución requiere internet para descargar el emulador. Se usa
el proyecto ficticio `demo-arribo`, no el ID del proyecto real. No cambiar el `.env`
de la app ni ejecutar `firebase login` para estas pruebas.

Las pruebas verifican las reglas locales, sin publicarlas:

- El propietario crea y lee su perfil y puede cambiar nombre y preferencias.
- `display_name` admite de 1 a 100 caracteres, con acentos y espacios interiores;
  rechaza valores no textuales, espacios exteriores, nombres vacíos y saltos de línea.
- El nombre es opcional temporalmente para versiones anteriores de la app y perfiles
  antiguos. Completarlo no altera fecha ni preferencias; una vez guardado no se elimina.
- Se rechazan correos que no coincidan con el token, fechas inventadas, cambios de
  `created_at`, campos extra y preferencias inválidas.
- Se impide acceder al perfil ajeno, listar todos los perfiles o eliminar el propio.
- Historial y feedback solo permiten lectura al propietario; sus escrituras móviles
  están bloqueadas. Alertas permiten lectura autenticada, no escritura móvil.
- La caché y las rutas desconocidas siguen bloqueadas para clientes.

Los datos mínimos insertados omitiendo reglas solo preparan casos de prueba locales;
no validan el contrato completo de los documentos del backend. Las pruebas de
migración demuestran que los permisos permiten completar un perfil antiguo, no que
la app ya ejecute esa migración. La validación del backend/IAM requiere otras pruebas.

Las identidades de Auth son simuladas por `@firebase/rules-unit-testing`; no hace
falta iniciar el emulador Auth ni crear cuentas reales. La limpieza entre pruebas
solo afecta a los datos del emulador del proyecto ficticio. Si `npm test` se ejecuta
sin `FIRESTORE_EMULATOR_HOST` local, falla expresamente para evitar usos ambiguos.

### Resultado de verificación local

El 17 de octubre de 2026 se ejecutó `npm run test:rules`: **55 pruebas aprobadas,
0 fallidas**. El emulador se detuvo al terminar. Esto no es un despliegue ni una
verificación contra la base real. Los mensajes `PERMISSION_DENIED` de las pruebas
negativas son esperados: comprueban que operaciones prohibidas sean rechazadas.

## 4. Inspección opcional

Para dejar el emulador abierto:

```bash
npm run emulators:firestore
```

Consultar la URL de la interfaz que muestra la terminal. En otra terminal, dentro
de `Codigo/Firebase`, se pueden ejecutar las pruebas contra ese emulador:

```bash
FIRESTORE_EMULATOR_HOST=127.0.0.1:8082 npm test
```

Detener con Ctrl+C. No iniciar simultáneamente dos emuladores en el puerto `8082`.

## Límites y siguiente paso

- Se modifican reglas locales; no se publican reglas, índices ni datos remotos.
- La app sigue conectada al proyecto configurado en su `.env`, no a este emulador.
- El emulador no exige índices compuestos; estos deben verificarse posteriormente
  contra un proyecto real de desarrollo.
- Siguiente paso: integrar la escritura y migración de `display_name` en la app,
  alineando sus validaciones con estas reglas. No habilitar la escritura en la app
  real hasta publicar reglas probadas en el proyecto de desarrollo correcto.
- El nombre sigue guardándose solo en Auth en la app actual; esta etapa no cambia eso.

## Auditoría de dependencias

La instalación inicial y `npm audit` detectaron 13 avisos (5 moderados y 8 altos)
en el árbol de herramientas de desarrollo. Hay dependencias transitivas afectadas;
algunas correcciones propuestas por npm implican volver a versiones mayores
anteriores de Firebase/CLI. No ejecutar `npm audit fix --force` automáticamente:
requiere revisar compatibilidad y volver a probar. Esta preparación no certifica
la seguridad de todas las dependencias ni cambia las de `App_Movil`.

Para consultar el estado actualizado, desde esta carpeta:

```bash
npm audit
```

Referencias oficiales:

- [Pruebas de reglas Firestore](https://firebase.google.com/docs/firestore/security/test-rules-emulator)
- [Conexión y límites del emulador](https://firebase.google.com/docs/emulator-suite/connect_firestore)
