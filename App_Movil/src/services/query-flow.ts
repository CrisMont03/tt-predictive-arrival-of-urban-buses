import type { Prediction } from '../types/arribo';
import { resolveStationPrediction, StaleStationPrediction, type StationSnapshot } from '../utils/station-selection.ts';

type Consultation = { uid: string; snapshot: StationSnapshot; isCurrent: () => boolean;
  send: (id: string) => Promise<Prediction>; onResult: (result: Prediction) => void };

export function createQueryFlow(newId = () => `query-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`) {
  let generation = 0;
  let pending: { id: string; uid: string; snapshot: StationSnapshot } | null = null;
  let flight: { id: string; promise: Promise<Prediction> } | null = null;
  return {
    reset() { generation++; pending = null; flight = null; },
    run({ uid, snapshot, isCurrent, send, onResult }: Consultation): Promise<Prediction> {
      if (!pending || pending.uid !== uid || pending.snapshot.stationId !== snapshot.stationId || pending.snapshot.revision !== snapshot.revision) {
        pending = { id: newId(), uid, snapshot };
      }
      const request = pending;
      if (flight?.id === request.id) return flight.promise;
      const captured = generation;
      const current = () => generation === captured && isCurrent();
      const promise = resolveStationPrediction(snapshot.stationId, current, () => send(request.id)).then(result => {
        if (!current()) throw new StaleStationPrediction();
        if (result.query_id !== request.id) throw new Error('La respuesta no corresponde a la consulta enviada.');
        onResult(result);
        if (pending === request) pending = null;
        return result;
      }).finally(() => { if (flight?.id === request.id) flight = null; });
      flight = { id: request.id, promise };
      return promise;
    },
  };
}
