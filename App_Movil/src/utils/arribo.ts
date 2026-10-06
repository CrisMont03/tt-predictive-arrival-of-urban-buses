import { TIME_ZONE } from '@/config/arribo';
export { averageByHour, distanceMeters, parseMinutes } from './metrics';

export function formatDate(value: string) {
  return new Intl.DateTimeFormat('es-MX', { timeZone: TIME_ZONE, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}
