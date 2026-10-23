import type { Prediction } from '../types/arribo';

type ExpectedQuery = { queryId: string; stationId: string };
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const between = (value: unknown, min: number, max: number) => finite(value) && value >= min && value <= max;
const binary = (value: unknown) => value === 0 || value === 1;
const date = (value: unknown) => typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) && Number.isFinite(Date.parse(value));

export function parsePrediction(value: unknown, expected?: ExpectedQuery): Prediction {
  if (!record(value) || typeof value.query_id !== 'string' || !value.query_id ||
      typeof value.station_id !== 'string' || !value.station_id ||
      typeof value.model_version !== 'string' || !value.model_version.trim() ||
      !between(value.waiting_time_min, 0, 45) ||
      !Array.isArray(value.confidence_interval) || value.confidence_interval.length !== 2 ||
      !value.confidence_interval.every(n => finite(n) && n >= 0) || value.confidence_interval[0] > value.confidence_interval[1] ||
      !date(value.timestamp) || !date(value.expires_at) ||
      (value.requested_at !== undefined && !date(value.requested_at)) ||
      Date.parse(value.expires_at as string) <= Date.parse(value.timestamp as string) ||
      !['mock', 'cache', 'model'].includes(value.source as string) || typeof value.simulation !== 'boolean' ||
      (value.source === 'mock' && !value.simulation) || (value.source === 'model' && value.simulation) ||
      !record(value.context)) throw new Error('Respuesta de predicción inválida.');
  const c = value.context;
  if (!between(c.hour, 0, 23) || !Number.isInteger(c.hour) || !between(c.day_of_week, 0, 6) || !Number.isInteger(c.day_of_week) ||
      !binary(c.is_weekend) || !(c.is_holiday === null || binary(c.is_holiday)) ||
      !(c.precipitation_mm === null || (finite(c.precipitation_mm) && c.precipitation_mm >= 0)) ||
      !(c.temp_c === null || finite(c.temp_c)) || !(c.humidity === null || between(c.humidity, 0, 100)) ||
      !(c.traffic_density === null || between(c.traffic_density, 0, 1)) ||
      !(c.env_alert_active === null || binary(c.env_alert_active)) ||
      (value.context_sources != null && (!record(value.context_sources) || !Object.values(value.context_sources).every(record)))) {
    throw new Error('El contexto de la predicción está incompleto.');
  }
  if (expected && (value.query_id !== expected.queryId || value.station_id !== expected.stationId || !date(value.requested_at))) {
    throw new Error('La respuesta no corresponde a la consulta enviada.');
  }
  return value as unknown as Prediction;
}

export function toISO(value: unknown): string {
  if (value instanceof Date) return value.toISOString();
  if (record(value) && typeof value.toDate === 'function') return (value.toDate() as Date).toISOString();
  return String(value);
}

export function normalizePredictionDates(data: Record<string, unknown>, queryId: string) {
  return { ...data, query_id: queryId, timestamp: toISO(data.timestamp),
    requested_at: toISO(data.requested_at ?? data.timestamp), expires_at: toISO(data.expires_at) };
}

export function assertFeedbackAcknowledgement(value: unknown, queryId: string) {
  if (!record(value) || value.status !== 'received' || value.query_id !== queryId ||
      typeof value.simulation !== 'boolean' || typeof value.outside_domain !== 'boolean') {
    throw new Error('No se pudo confirmar el reporte. Reintenta con la misma consulta.');
  }
}

export function retryableFeedbackError(error: unknown) {
  return record(error) && (error.code === 'NETWORK' || error.code === 'INVALID_RESPONSE' ||
    error.status === 408 || error.status === 429 || (finite(error.status) && error.status >= 500));
}
