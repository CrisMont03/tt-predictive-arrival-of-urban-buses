export const STATION = {
  id: 'rio_consulado',
  name: 'Río Consulado – Misterios',
  city: 'Ciudad de México',
  latitude: 19.4624,
  longitude: -99.1297,
};
export const TIME_ZONE = 'America/Mexico_City';
export const DEMO_MODE = process.env.EXPO_PUBLIC_APP_MODE !== 'live';
export const API_URL = (process.env.EXPO_PUBLIC_API_URL ?? '').replace(/\/$/, '');
export const DEFAULT_PREFERENCES = {
  preferred_station: STATION.id,
  gps_enabled: false,
  notifications_enabled: false,
};
