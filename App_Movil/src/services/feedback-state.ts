import type { HistoryEntry, PendingFeedback } from '../types/arribo';

export function withPendingFeedback(history: HistoryEntry[], pending: PendingFeedback[]): HistoryEntry[] {
  const byQuery = new Map(pending.map(report => [report.query_id, report]));
  return history.map(entry => {
    const report = byQuery.get(entry.query_id);
    return report && report.station_id === entry.station_id
      ? { ...entry, actual_min: report.actual_min, feedback_pending: true } : entry;
  });
}

export function enqueueFeedback(queue: PendingFeedback[], report: PendingFeedback): PendingFeedback[] {
  const existing = queue.find(item => item.query_id === report.query_id);
  if (existing && (existing.actual_min !== report.actual_min || existing.station_id !== report.station_id)) {
    throw new Error('Ya existe un reporte pendiente para esta consulta. Reintenta su envío sin cambiarlo.');
  }
  return existing ? [...queue] : [...queue, report];
}
