# ARRIBO — aplicación móvil

Expo SDK 57, TypeScript y Expo Router. Pantallas en `src/app`: acceso, Predicción, Historial, Alertas, Configuración y reporte de espera.

## Probar sin modelo ni Firebase

```sh
npm ci
npx expo start --go
```

Abre la app con Expo Go compatible con SDK 57 y toca **Explorar demostración**. El modo predeterminado es `demo`; todos los resultados se identifican como ejemplos. Configuración permite simular demora, error y listas vacías. No se envía ningún dato a Firebase ni al backend en esta modalidad. Consultas, preferencias y reportes demo se guardan localmente.

## Conectar servicios reales

1. Crea `.env` usando `.env.example` y selecciona `EXPO_PUBLIC_APP_MODE=live`.
2. Configura Firebase público (apiKey, authDomain, projectId, appId); nunca pongas una cuenta de servicio en `EXPO_PUBLIC_*`.
3. Habilita Email/Password en Firebase Auth y Firestore. Publica reglas/índices de `../Firebase` después de probarlos.
4. Configura `EXPO_PUBLIC_API_URL`. Desde el teléfono, usa la IP LAN del servidor o HTTPS público, no `localhost`. Reinicia Expo después de cambiar variables.
5. El backend debe verificar tokens del mismo proyecto. Sin modelo aprobado, una consulta mostrará el error real de indisponibilidad.

GPS solicita permiso solo al activarlo; negarlo no bloquea las consultas. Historial y preferencias se aíslan por uid. Los reportes reales sin red se guardan en una cola y se reintentan al reconectar. No uses un backend `demo` con la app `live`: sus esquemas de autenticación son intencionalmente distintos.

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
