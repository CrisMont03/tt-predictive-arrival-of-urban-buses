import assert from 'node:assert/strict';
import test from 'node:test';
import { enqueueFeedback } from '../src/services/feedback-state.ts';
import { feedbackPayload } from '../src/utils/station-selection.ts';

test('Feedback confirma origen, conserva cola y bloquea cambios del reporte pendiente', () => {
  const report = { query_id: 'query-0001', actual_min: 15, station_id: 'rio_consulado', wait_basis: 'since_query' as const };
  const restored = JSON.parse(JSON.stringify(enqueueFeedback([], report)));
  assert.equal(restored[0].wait_basis, 'since_query');
  assert.deepEqual(feedbackPayload(restored[0]), { query_id: report.query_id, actual_min: 15, wait_basis: 'since_query' });
  assert.equal(enqueueFeedback(restored, report).length, 1);
  assert.throws(() => enqueueFeedback(restored, { ...report, wait_basis: 'unspecified' }));
});

test('Feedback antiguo mantiene payload y no se declara confirmado durante la migración', () => {
  const old = { query_id: 'query-old1', actual_min: 5, station_id: 'rio_consulado' };
  assert.deepEqual(feedbackPayload(old), { query_id: 'query-old1', actual_min: 5 });
  assert.equal(enqueueFeedback([old], { ...old, wait_basis: 'unspecified' }).length, 1);
});
