/// <reference types="node" />
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Prediction, Station } from '../src/types/arribo';
import { DEFAULT_STATION, filterStations, initialStation, stationAvailable, stationName, STATIONS } from '../src/data/stations.ts';
import { createStationSelection, feedbackPayload, resolveStationPrediction, StaleStationPrediction } from '../src/utils/station-selection.ts';
import { stationPickerReducer } from '../src/utils/station-picker.ts';

test('Catálogo: IDs únicos, coordenadas válidas y únicamente rio_consulado real', () => {
  assert.equal(new Set(STATIONS.map((station: Station) => station.id)).size, STATIONS.length);
  assert.deepEqual(STATIONS.filter((station: Station) => stationAvailable(station, 'live')).map((station: Station) => station.id), ['rio_consulado']);
  for (const station of STATIONS) {
    assert.ok(Math.abs(station.latitude) <= 90 && Math.abs(station.longitude) <= 180);
    assert.ok(station.name && station.zone);
    if (station.id !== 'rio_consulado') { assert.equal(station.demonstration, true); assert.equal(stationAvailable(station, 'live'), false); }
  }
});
test('Búsqueda por nombre/zona ignora mayúsculas, acentos y espacios', () => {
  assert.deepEqual(filterStations('  RIO CONSULÁDO ').map((station: Station) => station.id), ['rio_consulado']);
  assert.deepEqual(filterStations('ZÓNA NÓRTE').map((station: Station) => station.id), ['demo_norte']);
  assert.deepEqual(filterStations('misterios MÉXICO').map((station: Station) => station.id), ['rio_consulado']);
  assert.deepEqual(filterStations('inexistente'), []);
  assert.equal(filterStations('   ').length, STATIONS.length);
});
test('La favorita inicial debe existir y estar habilitada en el modo actual', () => {
  assert.equal(initialStation('demo_norte', 'demo').id, 'demo_norte');
  assert.equal(initialStation('demo_norte', 'live').id, 'rio_consulado');
  assert.equal(initialStation('inexistente', 'demo').id, 'rio_consulado');
  assert.equal(initialStation(undefined, 'live').id, 'rio_consulado');
});
test('La selección de consulta no modifica favorita ni pierde la elección por una carga tardía', () => {
  const selection = createStationSelection(DEFAULT_STATION); const preferred = { preferred_station: 'rio_consulado' };
  selection.select(STATIONS[1]); selection.applyPreference(initialStation(preferred.preferred_station, 'demo'));
  assert.equal(selection.current().id, 'demo_norte'); assert.equal(preferred.preferred_station, 'rio_consulado');
  assert.equal(selection.reset().id, 'rio_consulado');
  assert.equal(selection.applyPreference(STATIONS[2]).id, 'demo_centro');
});
test('Una misma estación seleccionada no invalida la consulta; A → B → A sí', () => {
  const selection = createStationSelection(DEFAULT_STATION); const snapshot = selection.capture();
  selection.select(DEFAULT_STATION); assert.equal(selection.matches(snapshot), true);
  selection.select(STATIONS[1]); selection.select(DEFAULT_STATION); assert.equal(selection.matches(snapshot), false);
});
test('Estaciones no soportadas siguen bloqueadas aunque alguien cambie su flag live', () => {
  const edited = { ...STATIONS[1], demonstration: false, predictions: { live: true, demo: true } };
  assert.equal(stationAvailable(edited, 'live'), false);
});
test('Respuesta tardía se descarta y no puede sobrescribir la nueva estación', async () => {
  const selection = createStationSelection(DEFAULT_STATION); const snapshot = selection.capture();
  let resolve!: (result: Prediction) => void;
  const pending = resolveStationPrediction(snapshot.stationId, () => selection.matches(snapshot), () => new Promise<Prediction>(done => { resolve = done; }));
  selection.select(STATIONS[1]);
  resolve({ station_id: 'rio_consulado' } as Prediction);
  await assert.rejects(pending, StaleStationPrediction);
  const fresh = { station_id: 'demo_norte' } as Prediction; const next = selection.capture();
  assert.equal(await resolveStationPrediction(next.stationId, () => selection.matches(next), async () => fresh), fresh);
});
test('Errores de consultas antiguas se descartan y una respuesta de otra estación se rechaza', async () => {
  await assert.rejects(() => resolveStationPrediction('rio_consulado', () => false, async () => { throw new Error('network'); }), StaleStationPrediction);
  await assert.rejects(() => resolveStationPrediction('rio_consulado', () => true, async () => ({ station_id: 'demo_norte' } as Prediction)), /otra estación/);
  await assert.rejects(() => resolveStationPrediction('rio_consulado', () => false, async () => ({ station_id: 'rio_consulado' } as Prediction)), StaleStationPrediction);
});
test('Feedback conserva estación original localmente sin enviar campos extra al backend', () => {
  const report = { query_id: 'query-original', actual_min: 14, station_id: 'rio_consulado' };
  const selection = createStationSelection(DEFAULT_STATION); selection.select(STATIONS[1]);
  assert.deepEqual(feedbackPayload(report), { query_id: 'query-original', actual_min: 14 });
  assert.equal(report.station_id, 'rio_consulado');
  assert.equal(stationName('demo_norte'), 'Estación Norte');
  assert.equal(stationName('future-id'), 'future-id');
});
test('Abrir, buscar, cerrar y reabrir limpia la búsqueda del modal', () => {
  let state = stationPickerReducer({ visible: false, search: '' }, { type: 'open' });
  state = stationPickerReducer(state, { type: 'search', value: 'NORTE' });
  assert.equal(state.visible, true); assert.equal(filterStations(state.search)[0].id, 'demo_norte');
  // Button, backdrop, Android Back and selection all dispatch the same close.
  state = stationPickerReducer(state, { type: 'close' }); assert.deepEqual(state, { visible: false, search: '' });
  state = stationPickerReducer(state, { type: 'open' }); assert.equal(filterStations(state.search).length, STATIONS.length);
});
