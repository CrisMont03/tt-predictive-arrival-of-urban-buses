/// <reference types="node" />
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { PasswordValidationStatus } from 'firebase/auth';
import { emailError, loginErrors, passwordRecommendations, passwordRequirements, registrationErrors } from '../src/utils/auth-validation.ts';
import { completeRegistration } from '../src/services/registration.ts';

test('Correo: espacios exteriores permitidos, vacío y formato incorrecto rechazados', () => {
  assert.equal(emailError('  ana@correo.com  '), undefined);
  for (const email of ['', ' ', 'ana', 'ana@', 'ana @correo.com', 'ana@correo']) assert.ok(emailError(email));
});
test('Inicio no impone la complejidad de una contraseña nueva', () => {
  assert.deepEqual(loginErrors('ana@correo.com', 'x'), { email: undefined, password: undefined });
  assert.ok(loginErrors('ana@correo.com', '').password);
  assert.equal(loginErrors('ana@correo.com', ' ').password, undefined); // Do not trim passwords.
});
test('Registro admite nombres con acentos y espacios y exige confirmación exacta', () => {
  assert.ok(Object.values(registrationErrors('María José', 'ana@correo.com', 'Clave segura', 'Clave segura')).every(value => !value));
  assert.ok(registrationErrors('  ', 'ana@correo.com', 'abc', 'abc').name);
  assert.ok(registrationErrors('Ana', 'ana@correo.com', 'abc ', 'abc').confirmation);
  assert.ok(registrationErrors('Ana', 'ana@correo.com', '', '').password);
});
test('Requisitos corresponden a Firebase, no a reglas inventadas en el formulario', () => {
  const status = { isValid: false, meetsMinPasswordLength: false, containsUppercaseLetter: true, containsNumericCharacter: false, passwordPolicy: { customStrengthOptions: { minPasswordLength: 12 } } } as PasswordValidationStatus;
  assert.deepEqual(passwordRequirements(status), [
    { label: 'Al menos 12 caracteres', met: false }, { label: 'Una letra mayúscula', met: true }, { label: 'Un número', met: false },
  ]);
});
test('Registro completo crea Auth una vez y completa pasos del perfil en orden', async () => {
  const calls: string[] = []; const user = { uid: 'test' };
  const result = await completeRegistration({ create: async () => { calls.push('auth'); return user; }, saveName: async value => { assert.equal(value, user); calls.push('name'); }, ensureProfile: async () => { calls.push('profile'); } });
  assert.deepEqual(calls, ['auth', 'name', 'profile']); assert.deepEqual(result, { user, pending: [] });
});

test('Política: muestra los requisitos efectivos seleccionados sin convertirlos en recomendaciones', () => {
  const status = { isValid: false, meetsMinPasswordLength: true, meetsMaxPasswordLength: true,
    containsLowercaseLetter: true, containsUppercaseLetter: false, containsNumericCharacter: false,
    containsNonAlphanumericCharacter: false, passwordPolicy: {
      customStrengthOptions: { minPasswordLength: 8, maxPasswordLength: 64 },
      allowedNonAlphanumericCharacters: '!@#',
    },
  } as PasswordValidationStatus;
  assert.deepEqual(passwordRequirements(status).map(check => check.label), [
    'Al menos 8 caracteres', 'Una letra minúscula',
    'Una letra mayúscula', 'Un número', 'Un carácter especial',
  ]);
  assert.deepEqual(passwordRecommendations(status, 'abc'), []);
});

test('Política de seis caracteres no inventa complejidad obligatoria; la separa como recomendación', () => {
  const status = { isValid: true, meetsMinPasswordLength: true, passwordPolicy: {
    customStrengthOptions: { minPasswordLength: 6 }, allowedNonAlphanumericCharacters: '!@#',
  } } as PasswordValidationStatus;
  assert.deepEqual(passwordRequirements(status), [{ label: 'Al menos 6 caracteres', met: true }]);
  const suggestions = passwordRecommendations(status, 'Arribo12!');
  assert.deepEqual(suggestions.map(check => check.label), ['Una letra mayúscula', 'Un número', 'Un carácter especial']);
  assert.ok(suggestions.every(check => check.met));
  assert.ok(passwordRecommendations(status, 'aaaaaa').some(check => !check.met));
});
test('Firestore rechazado conserva la cuenta y no repite createUser', async () => {
  let created = 0;
  const result = await completeRegistration({ create: async () => { created++; return { uid: 'test' }; }, saveName: async () => {}, ensureProfile: async () => { throw new Error('permission-denied'); } });
  assert.equal(created, 1); assert.equal(result.user.uid, 'test'); assert.deepEqual(result.pending, ['profile']);
});
test('Fallo al guardar nombre se diferencia de fallo al crear cuenta', async () => {
  const result = await completeRegistration({ create: async () => ({ uid: 'test' }), saveName: async () => { throw new Error('offline'); }, ensureProfile: async () => {} });
  assert.deepEqual(result.pending, ['name']);
});
test('Fallo Auth no intenta guardar nombre ni perfil; permite reintentar el formulario', async () => {
  let profileCalls = 0;
  await assert.rejects(() => completeRegistration({ create: async () => { throw new Error('email-already-in-use'); }, saveName: async () => { profileCalls++; }, ensureProfile: async () => { profileCalls++; } }), /email-already-in-use/);
  assert.equal(profileCalls, 0);
});
