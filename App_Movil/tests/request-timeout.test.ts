/// <reference types="node" />
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { RequestTimeoutError, withRequestTimeout } from '../src/services/request-timeout.ts';

test('Una respuesta de arranque lento supera los 12 segundos anteriores y se recibe', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let signal!: AbortSignal;
  let finish!: (value: string) => void;
  const request = withRequestTimeout(s => {
    signal = s;
    return new Promise<string>(resolve => { finish = resolve; });
  });
  t.mock.timers.tick(52_000);
  assert.equal(signal.aborted, false);
  finish('predicción real');
  assert.equal(await request, 'predicción real');
  t.mock.timers.tick(90_000);
  assert.equal(signal.aborted, false, 'Se cancela el temporizador tras recibir la respuesta');
});

test('A los 90 segundos se cancela la petición con un error de demora específico', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let signal!: AbortSignal;
  const request = withRequestTimeout(s => {
    signal = s;
    return new Promise<never>((_, reject) => {
      s.addEventListener('abort', () => reject(new Error('AbortError')), { once: true });
    });
  });
  const rejected = assert.rejects(request, (error: unknown) => {
    assert.ok(error instanceof RequestTimeoutError);
    assert.match(error.message, /tardó demasiado/);
    assert.doesNotMatch(error.message, /Comprueba tu conexión/);
    return true;
  });
  t.mock.timers.tick(89_999);
  assert.equal(signal.aborted, false);
  t.mock.timers.tick(1);
  await rejected;
  assert.equal(signal.aborted, true);
});

test('La demora también limita la renovación de sesión y conserva otros errores', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const pendingToken = withRequestTimeout(() => new Promise<never>(() => {}));
  const rejected = assert.rejects(pendingToken, RequestTimeoutError);
  t.mock.timers.tick(90_000);
  await rejected;
  const network = new TypeError('Network request failed');
  await assert.rejects(withRequestTimeout(async () => { throw network; }), error => error === network);
});
