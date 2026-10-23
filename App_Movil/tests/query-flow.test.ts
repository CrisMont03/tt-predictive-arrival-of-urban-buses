/// <reference types="node" />
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Prediction } from '../src/types/arribo';
import { createQueryFlow } from '../src/services/query-flow.ts';
import { assertFeedbackAcknowledgement, normalizePredictionDates, parsePrediction, retryableFeedbackError } from '../src/services/prediction-contract.ts';
import { StaleStationPrediction } from '../src/utils/station-selection.ts';
import { enqueueFeedback, withPendingFeedback } from '../src/services/feedback-state.ts';

function prediction(changes: Partial<Prediction> = {}): Prediction {
  return { query_id: 'query-unit-1', station_id: 'rio_consulado', waiting_time_min: 12,
    confidence_interval: [8, 16], model_version: 'integration-only', source: 'mock', simulation: true,
    timestamp: '2026-10-20T14:00:00Z', requested_at: '2026-10-20T14:00:00Z', expires_at: '2026-10-20T14:05:00Z',
    context: { hour: 8, day_of_week: 1, is_weekend: 0, is_holiday: 0, precipitation_mm: null,
      temp_c: 20, humidity: 65, traffic_density: null, env_alert_active: null }, ...changes };
}

test('Contrato: acepta miss simulado, hit simulado y resultado real sin confundir sus fuentes', () => {
  const expected = { queryId: 'query-unit-1', stationId: 'rio_consulado' };
  assert.equal(parsePrediction(prediction(), expected).simulation, true);
  assert.equal(parsePrediction(prediction({ source: 'cache' }), expected).simulation, true);
  assert.equal(parsePrediction(prediction({ source: 'model', simulation: false }), expected).simulation, false);
  const sources = { weather: { status: 'available', valid_until: '2026-10-20T14:05:00Z' } };
  assert.deepEqual(parsePrediction(prediction({ context_sources: sources })).context_sources, sources);
});

test('Contrato: rechaza respuestas incompletas, no finitas, ajenas o falsamente reales', () => {
  const bad: unknown[] = [null, {}, { ...prediction(), context: {} }, prediction({ waiting_time_min: NaN }),
    prediction({ waiting_time_min: 46 }), prediction({ confidence_interval: [15, 8] }),
    prediction({ timestamp: 'invalid' }), prediction({ expires_at: prediction().timestamp }),
    prediction({ source: 'mock', simulation: false }), prediction({ source: 'model', simulation: true }),
    prediction({ model_version: '' }), prediction({ context: { ...prediction().context, humidity: 101 } })];
  for (const value of bad) assert.throws(() => parsePrediction(value));
  assert.throws(() => parsePrediction(prediction(), { queryId: 'other-query', stationId: 'rio_consulado' }));
  assert.throws(() => parsePrediction(prediction(), { queryId: 'query-unit-1', stationId: 'other-station' }));
  assert.throws(() => parsePrediction(prediction({ requested_at: undefined }), { queryId: 'query-unit-1', stationId: 'rio_consulado' }));
});

test('Historial: normaliza Timestamp y conserva procedencia; una predicción vencida sigue siendo historial válido', () => {
  const value = prediction();
  const timestamp = (date: string) => ({ toDate: () => new Date(date) });
  const normalized = normalizePredictionDates({ ...value, requested_at: undefined,
    timestamp: timestamp(value.timestamp), expires_at: timestamp(value.expires_at) }, value.query_id);
  assert.equal(normalized.requested_at, new Date(value.timestamp).toISOString());
  assert.equal(parsePrediction(normalized).query_id, value.query_id);
});

test('Consulta: doble toque comparte solicitud, reintento reutiliza ID y consulta nueva genera otro', async () => {
  let sequence = 0; const flow = createQueryFlow(() => `query-unit-${++sequence}`);
  const ids: string[] = []; const history: Prediction[] = [];
  const base = { uid: 'alice', snapshot: { stationId: 'rio_consulado', revision: 1 },
    isCurrent: () => true, onResult: (result: Prediction) => { history.push(result); } };
  const failed = () => flow.run({ ...base, send: async id => { ids.push(id); throw new Error('network'); } });
  await assert.rejects(failed(), /network/);
  let resolve!: (value: Prediction) => void;
  const send = (id: string) => { ids.push(id); return new Promise<Prediction>(done => { resolve = done; }); };
  const first = flow.run({ ...base, send }); const duplicate = flow.run({ ...base, send });
  assert.equal(first, duplicate);
  resolve(prediction()); await first;
  assert.deepEqual(ids, ['query-unit-1', 'query-unit-1']);
  assert.equal(history.length, 1);
  await flow.run({ ...base, send: async id => prediction({ query_id: id }) });
  assert.equal(sequence, 2); assert.equal(history.length, 2);
});

test('Consulta: reset de sesión descarta respuesta antigua y no borra el reintento de la sesión nueva', async () => {
  let sequence = 0; const flow = createQueryFlow(() => `query-unit-${++sequence}`);
  let resolve!: (value: Prediction) => void;
  const stored: Prediction[] = [];
  const base = { uid: 'alice', snapshot: { stationId: 'rio_consulado', revision: 1 }, isCurrent: () => true,
    onResult: (result: Prediction) => { stored.push(result); } };
  const old = flow.run({ ...base, send: () => new Promise<Prediction>(done => { resolve = done; }) });
  flow.reset();
  await assert.rejects(flow.run({ ...base, uid: 'bob', send: async () => { throw new Error('network'); } }));
  resolve(prediction()); await assert.rejects(old, StaleStationPrediction);
  await flow.run({ ...base, uid: 'bob', send: async id => { assert.equal(id, 'query-unit-2'); return prediction({ query_id: id }); } });
  assert.equal(stored.length, 1);
});

test('Consulta: una respuesta de otro request_id no crea historial y permite reintentar', async () => {
  const flow = createQueryFlow(() => 'query-unit-1'); let inserted = 0;
  const base = { uid: 'alice', snapshot: { stationId: 'rio_consulado', revision: 1 }, isCurrent: () => true,
    onResult: () => { inserted++; } };
  await assert.rejects(flow.run({ ...base, send: async () => prediction({ query_id: 'wrong-id' }) }), /no corresponde/);
  assert.equal(inserted, 0);
  await flow.run({ ...base, send: async id => prediction({ query_id: id }) });
  assert.equal(inserted, 1);
});

test('Feedback: solo confirma acuse válido; red, 503 y respuesta perdida permiten conservar envío pendiente', () => {
  assert.doesNotThrow(() => assertFeedbackAcknowledgement({ status: 'received', query_id: 'query-unit-1', simulation: true, outside_domain: false }, 'query-unit-1'));
  for (const value of [{}, null, { status: 'received', query_id: 'wrong-id', simulation: true, outside_domain: false }]) {
    assert.throws(() => assertFeedbackAcknowledgement(value, 'query-unit-1'));
  }
  for (const error of [{ code: 'NETWORK', status: 0 }, { code: 'SERVICE_ERROR', status: 503 }, { code: 'INVALID_RESPONSE', status: 200 }]) {
    assert.equal(retryableFeedbackError(error), true);
  }
  for (const status of [401, 403, 404, 409, 422]) assert.equal(retryableFeedbackError({ code: 'ERROR', status }), false);
});

test('Feedback: al restaurar sin conexión conserva pendiente, estación original y valor sin duplicar ni reemplazar', () => {
  const report = { query_id: 'query-unit-1', actual_min: 15, station_id: 'rio_consulado' };
  const first = enqueueFeedback([], report);
  assert.deepEqual(enqueueFeedback(first, report), first);
  assert.throws(() => enqueueFeedback(first, { ...report, actual_min: 16 }), /pendiente/);
  const restored = withPendingFeedback([prediction()], first);
  assert.equal(restored[0].actual_min, 15);
  assert.equal(restored[0].feedback_pending, true);
  assert.equal(restored[0].station_id, 'rio_consulado');
  assert.equal(withPendingFeedback([prediction()], [{ ...report, station_id: 'other' }])[0].feedback_pending, undefined);
});
