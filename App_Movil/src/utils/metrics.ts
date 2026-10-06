import type { HistoryEntry } from '@/types/arribo';

export function averageByHour(entries: HistoryEntry[]) {
  const groups = new Map<number, { total: number; count: number }>();
  entries.forEach(entry => {
    const hour = entry.context.hour;
    const previous = groups.get(hour) ?? { total: 0, count: 0 };
    groups.set(hour, { total: previous.total + entry.waiting_time_min, count: previous.count + 1 });
  });
  return [...groups].sort(([a], [b]) => a - b).map(([hour, group]) => ({ hour, average: group.total / group.count, count: group.count }));
}

export function distanceMeters(latitude: number, longitude: number, targetLat: number, targetLon: number) {
  const rad = (n: number) => n * Math.PI / 180;
  const a = Math.sin(rad(targetLat - latitude) / 2) ** 2 + Math.cos(rad(latitude)) * Math.cos(rad(targetLat)) * Math.sin(rad(targetLon - longitude) / 2) ** 2;
  return 6371000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0, 1 - a)));
}

export function parseMinutes(input: string): number | null {
  const normalized = input.trim().replace(',', '.');
  if (!/^\d+(\.\d{1,2})?$/.test(normalized)) return null;
  const value = Number(normalized);
  return Number.isFinite(value) && value >= 0 && value <= 180 ? value : null;
}
