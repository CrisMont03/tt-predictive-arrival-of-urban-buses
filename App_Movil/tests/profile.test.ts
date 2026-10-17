/// <reference types="node" />
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { planProfileWrite } from '../src/services/profile-plan.ts';
import { displayNameError, normalizedProfileName } from '../src/utils/profile-name.ts';

const preferences = { preferred_station: 'rio_consulado', gps_enabled: false, notifications_enabled: false };

test('Nombre: validación compartida coincide con longitud UTF-16 de las reglas', () => {
  for (const name of ['Ana', '  María José  ', 'A'.repeat(100), '🚌'.repeat(50)]) {
    assert.equal(displayNameError(name), undefined);
  }
  for (const name of ['', ' ', '\u00a0', 'A'.repeat(101), '🚌'.repeat(51), 'Ana\nMaría', 'Ana\rMaría', 'Ana\u2028María', 'Ana\u2029María']) {
    assert.ok(displayNameError(name));
  }
  assert.equal(normalizedProfileName('\u00a0María José\u00a0'), 'María José');
  assert.equal(normalizedProfileName(null), undefined);
  assert.equal(normalizedProfileName(123), undefined);
});

test('Perfil nuevo guarda nombre recortado y fecha del servidor sin campos de contraseña', () => {
  const timestamp = { serverTimestamp: true };
  const plan = planProfileWrite(undefined, 'ana@example.com', ' María José ', preferences, timestamp);
  assert.deepEqual(plan, { kind: 'create', data: {
    email: 'ana@example.com', display_name: 'María José', created_at: timestamp, preferences,
  } });
});

test('Migración solo añade nombre: no incluye fecha, correo ni preferencias en la actualización', () => {
  assert.deepEqual(planProfileWrite({}, 'ana@example.com', ' Ana ', preferences, 'unused'), {
    kind: 'update', data: { display_name: 'Ana' },
  });
});

test('Un nombre existente no se reemplaza aunque Auth o un reintento tengan otro valor', () => {
  assert.deepEqual(planProfileWrite({ display_name: 'Nombre actual' }, 'ana@example.com', 'Nombre anterior', preferences, 'unused'), { kind: 'none' });
});

test('Reintentos de registro y migración son idempotentes', () => {
  const first = planProfileWrite(undefined, 'ana@example.com', 'Ana', preferences, 'server-time');
  assert.equal(first.kind, 'create');
  if (first.kind !== 'create') return;
  assert.deepEqual(planProfileWrite(first.data, 'ana@example.com', 'Ana', preferences, 'new-time'), { kind: 'none' });
  assert.deepEqual(planProfileWrite({ display_name: 'Ana' }, 'ana@example.com', 'Ana', preferences, 'new-time'), { kind: 'none' });
});

test('Un perfil sin nombre no recibe valores inventados desde Auth inválido', () => {
  assert.deepEqual(planProfileWrite({}, 'ana@example.com', null, preferences, 'unused'), { kind: 'none' });
  assert.deepEqual(planProfileWrite({}, 'ana@example.com', 'Ana\nMaría', preferences, 'unused'), { kind: 'none' });
  const plan = planProfileWrite(undefined, 'ana@example.com', null, preferences, 'server-time');
  assert.equal(plan.kind, 'create');
  if (plan.kind === 'create') assert.ok(!('display_name' in plan.data));
  assert.throws(() => planProfileWrite(undefined, null, 'Ana', preferences, 'server-time'));
});
