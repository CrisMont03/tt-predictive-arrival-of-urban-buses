import { STATION, TIME_ZONE } from '@/config/arribo';
import type { Alert, Context, DemoScenario, HistoryEntry, Prediction } from '@/types/arribo';

export function demoContext(date = new Date()): Context {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: TIME_ZONE, hour: 'numeric', hourCycle: 'h23', weekday: 'short' }).formatToParts(date);
  const day = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(parts.find(p => p.type === 'weekday')?.value ?? 'Mon');
  return { hour: Number(parts.find(p => p.type === 'hour')?.value ?? 7), day_of_week: day, is_weekend: Number(day >= 5), is_holiday: 0, precipitation_mm: 0, temp_c: 19, humidity: 65, traffic_density: 0.72, env_alert_active: 0 };
}

export async function demoPrediction(requestId: string, scenario: DemoScenario): Promise<Prediction> {
  await new Promise(resolve => setTimeout(resolve, scenario === 'slow' ? 2500 : 450));
  if (scenario === 'error') throw new Error('El servicio de demostración no está disponible. Puedes reintentar o cambiar el escenario en Configuración.');
  const now = new Date();
  return { query_id: requestId, station_id: STATION.id, waiting_time_min: 12.3, confidence_interval: [8, 17], model_version: 'demo-v1', timestamp: now.toISOString(), expires_at: new Date(now.getTime() + 300000).toISOString(), source: 'mock', simulation: true, context: demoContext(now) };
}

export function demoHistory(): HistoryEntry[] {
  return [8.4, 15.2, 11.7].map((minutes, index) => {
    const date = new Date(Date.now() - (index + 1) * 3600000);
    return { query_id: `example-${index}`, station_id: STATION.id, waiting_time_min: minutes, confidence_interval: [Math.max(0, minutes - 4), minutes + 4], model_version: 'demo-v1', timestamp: date.toISOString(), expires_at: date.toISOString(), source: 'mock', simulation: true, context: demoContext(date), ...(index === 1 ? { actual_min: 16 } : {}) };
  });
}

export function demoAlerts(): Alert[] {
  return [{ id: 'demo-notice', station_id: STATION.id, type: 'mantenimiento', message: 'Este es un aviso de ejemplo. Las alertas reales de servicio aparecerán aquí cuando el sistema esté conectado.', severity: 'info', active: true, created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 7200000).toISOString() }];
}
