/// <reference types="node" />
import assert from 'node:assert/strict';
import { test } from 'node:test';
// @ts-expect-error Node's native TS runner requires the extension.
import { averageByHour, distanceMeters, parseMinutes } from '../src/utils/metrics.ts';
import type { HistoryEntry } from '../src/types/arribo';

test('Feedback acepta cero, coma decimal y 180; rechaza datos ambiguos', () => {
  assert.equal(parseMinutes('0'), 0);
  assert.equal(parseMinutes(' 14,5 '), 14.5);
  assert.equal(parseMinutes('180'), 180);
  for (const value of ['-1', '', 'Infinity', 'NaN', '1e2', '180.01', '1.234', '12abc', '.5']) assert.equal(parseMinutes(value), null);
});

test('Distancia: estación, umbral de 200 m y coordenadas lejanas', () => {
  assert.equal(distanceMeters(19.4624, -99.1297, 19.4624, -99.1297), 0);
  assert.ok(distanceMeters(19.4634, -99.1297, 19.4624, -99.1297) < 200);
  assert.ok(distanceMeters(19.4654, -99.1297, 19.4624, -99.1297) > 200);
});

test('Historial promedia predicciones, no reportes, y ordena las horas', () => {
  const rows = [
    { context: { hour: 8 }, waiting_time_min: 10, actual_min: 100 },
    { context: { hour: 7 }, waiting_time_min: 4 },
    { context: { hour: 8 }, waiting_time_min: 20 },
  ] as HistoryEntry[];
  assert.deepEqual(averageByHour(rows), [{ hour: 7, average: 4, count: 1 }, { hour: 8, average: 15, count: 2 }]);
  assert.deepEqual(averageByHour([]), []);
});
