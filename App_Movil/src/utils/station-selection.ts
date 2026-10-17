import type { PendingFeedback, Prediction, Station } from '../types/arribo';

export type StationSnapshot = { stationId: string; revision: number };
export function createStationSelection(defaultStation: Station) {
  let station = defaultStation; let revision = 0; let userSelected = false;
  function change(next: Station) {
    if (next.id !== station.id) { station = next; revision++; }
    return station;
  }
  return {
    current: () => station,
    capture: (): StationSnapshot => ({ stationId: station.id, revision }),
    matches: (snapshot: StationSnapshot) => snapshot.stationId === station.id && snapshot.revision === revision,
    select: (next: Station) => { userSelected = true; return change(next); },
    applyPreference: (preferred: Station) => userSelected ? station : change(preferred),
    reset: () => { station = defaultStation; userSelected = false; revision++; return station; },
  };
}
export class StaleStationPrediction extends Error {
  constructor() { super('La selección o la sesión cambió. Vuelve a consultar.'); }
}
export async function resolveStationPrediction(stationId: string, isCurrent: () => boolean, request: () => Promise<Prediction>) {
  let result: Prediction;
  try { result = await request(); }
  catch (failure) { if (!isCurrent()) throw new StaleStationPrediction(); throw failure; }
  if (!isCurrent()) throw new StaleStationPrediction();
  if (result.station_id !== stationId) throw new Error('El servicio devolvió una predicción de otra estación.');
  return result;
}
// Backend derives station_id from query_id. Local metadata is not sent.
export function feedbackPayload(report: Pick<PendingFeedback, 'query_id' | 'actual_min'>) {
  return { query_id: report.query_id, actual_min: report.actual_min };
}
