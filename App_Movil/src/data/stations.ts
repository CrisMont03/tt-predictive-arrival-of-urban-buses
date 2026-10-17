import type { Station } from '../types/arribo';

export const DEFAULT_STATION_ID = 'rio_consulado';
// Catalog changes alone cannot enable a station unsupported by the backend.
export const LIVE_SUPPORTED_STATION_IDS: readonly string[] = [DEFAULT_STATION_ID];
export const STATIONS: readonly Station[] = [
  { id: DEFAULT_STATION_ID, name: 'Río Consulado – Misterios', zone: 'Río Consulado · Ciudad de México', city: 'Ciudad de México', latitude: 19.4624, longitude: -99.1297, demonstration: false, predictions: { live: true, demo: true } },
  // Fictional examples; coordinates do not represent serviced stops.
  { id: 'demo_norte', name: 'Estación Norte', zone: 'Zona norte · ejemplo', city: 'Ciudad de México', latitude: 19.4744, longitude: -99.1297, demonstration: true, predictions: { live: false, demo: true } },
  { id: 'demo_centro', name: 'Estación Centro', zone: 'Zona centro · ejemplo', city: 'Ciudad de México', latitude: 19.4324, longitude: -99.1397, demonstration: true, predictions: { live: false, demo: true } },
];
export const DEFAULT_STATION: Station = STATIONS[0];
export function stationAvailable(station: Station, mode: 'demo' | 'live') {
  return mode === 'demo' ? station.predictions.demo : station.predictions.live && !station.demonstration && LIVE_SUPPORTED_STATION_IDS.includes(station.id);
}
export function stationById(id: string) { return STATIONS.find(station => station.id === id); }
export function stationName(id: string) { return stationById(id)?.name ?? id; }
export function initialStation(preferredId: string | undefined, mode: 'demo' | 'live') {
  const preferred = preferredId ? stationById(preferredId) : undefined;
  return preferred && stationAvailable(preferred, mode) ? preferred : DEFAULT_STATION;
}
export function normalizeStationSearch(value: string) {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es').trim();
}
export function filterStations(search: string) {
  const terms = normalizeStationSearch(search).split(/\s+/).filter(Boolean);
  return STATIONS.filter(station => terms.every(term => normalizeStationSearch(`${station.name} ${station.zone}`).includes(term)));
}
