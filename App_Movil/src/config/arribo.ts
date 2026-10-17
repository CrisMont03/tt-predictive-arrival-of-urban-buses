import { DEFAULT_STATION } from '@/data/stations';
// Compatibility alias for the default/favorite, not query selection.
export const STATION = DEFAULT_STATION;
export const TIME_ZONE = 'America/Mexico_City';
export const DEMO_MODE = process.env.EXPO_PUBLIC_APP_MODE !== 'live';
export const API_URL = (process.env.EXPO_PUBLIC_API_URL ?? '').replace(/\/$/, '');
export const DEFAULT_PREFERENCES = {
  preferred_station: STATION.id,
  gps_enabled: false,
  notifications_enabled: false,
};
