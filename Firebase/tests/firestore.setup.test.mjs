import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { after, before, beforeEach, test } from 'node:test';
import { planProfileWrite } from '../../App_Movil/src/services/profile-plan.ts';
import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
} from '@firebase/rules-unit-testing';
import {
  collection, deleteDoc, deleteField, doc, getDoc, getDocs, query,
  runTransaction, serverTimestamp, setDoc, Timestamp, updateDoc, where,
} from 'firebase/firestore';

// Guard before initialization: never point this harness at a real service.
const emulatorHost = process.env.FIRESTORE_EMULATOR_HOST;
assert.ok(
  emulatorHost === '127.0.0.1:8082' || emulatorHost === 'localhost:8082',
  'Ejecuta npm run test:rules; estas pruebas requieren el emulador local en 8082.'
);

let environment;

before(async () => {
  environment = await initializeTestEnvironment({
    projectId: 'demo-arribo',
    firestore: {
      host: '127.0.0.1',
      port: 8082,
      rules: readFileSync(new URL('../firestore.rules', import.meta.url), 'utf8'),
    },
  });
});

beforeEach(async () => environment.clearFirestore());
after(async () => environment?.cleanup());

function database(uid = 'ana') {
  return environment.authenticatedContext(uid, {
    email: `${uid}@example.com`,
  }).firestore();
}

function profile(overrides = {}) {
  return {
    email: 'ana@example.com',
    display_name: 'María José',
    created_at: serverTimestamp(),
    preferences: {
      preferred_station: 'rio_consulado',
      gps_enabled: false,
      notifications_enabled: false,
    },
    ...overrides,
  };
}

async function seed(path, data) {
  await environment.withSecurityRulesDisabled(async context => {
    await setDoc(doc(context.firestore(), path), data);
  });
}

test('Perfil: el propietario crea y lee su perfil con nombre', async () => {
  const reference = doc(database(), 'users', 'ana');
  await assertSucceeds(setDoc(reference, profile()));
  const snapshot = await assertSucceeds(getDoc(reference));
  assert.equal(snapshot.data().email, 'ana@example.com');
  assert.equal(snapshot.data().display_name, 'María José');
  assert.ok(snapshot.data().created_at instanceof Timestamp);
});

test('Perfil: otro usuario no puede leer, actualizar ni eliminar el perfil ajeno', async () => {
  await assertSucceeds(setDoc(doc(database(), 'users', 'ana'), profile()));
  const otherRef = doc(database('luis'), 'users', 'ana');
  await assertFails(getDoc(otherRef));
  await assertFails(updateDoc(otherRef, { display_name: 'Luis' }));
  await assertFails(deleteDoc(otherRef));
});

test('Perfil: no se puede crear un perfil en el UID de otra persona', async () => {
  await assertFails(setDoc(doc(database('luis'), 'users', 'ana'),
    profile({ email: 'luis@example.com' })));
});

test('Perfil: sin sesión no se puede leer, crear, actualizar ni eliminar', async () => {
  const anonymous = environment.unauthenticatedContext().firestore();
  await assertFails(setDoc(doc(anonymous, 'users', 'ana'), profile()));
  await seed('users/ana', profile({ created_at: Timestamp.fromMillis(1000) }));
  const reference = doc(anonymous, 'users', 'ana');
  await assertFails(getDoc(reference));
  await assertFails(updateDoc(reference, { display_name: 'Ana' }));
  await assertFails(deleteDoc(reference));
});

for (const name of ['A', 'María José', "D’Ávila López", 'A'.repeat(100), '🚌'.repeat(50)]) {
  test(`Nombre válido: ${name.length === 100 ? 'límite de 100 caracteres' : name}`, async () => {
    await assertSucceeds(setDoc(doc(database(), 'users', 'ana'),
      profile({ display_name: name })));
  });
}

const invalidNames = [
  ['vacío', ''], ['espacios', '   '], ['tabulaciones', '\t\t'],
  ['espacio no separable', '\u00a0'], ['más de 100 caracteres', 'a'.repeat(101)],
  ['más de 100 unidades UTF-16 con emojis', '🚌'.repeat(51)],
  ['espacio inicial', ' Ana'], ['espacio final', 'Ana '],
  ['salto de línea', 'Ana\nMaría'], ['retorno de carro', 'Ana\rMaría'],
  ['separador Unicode de línea', 'Ana\u2028María'],
  ['separador Unicode de párrafo', 'Ana\u2029María'],
  ['null', null], ['número', 123], ['booleano', true], ['mapa', {}], ['lista', []],
];
for (const [label, name] of invalidNames) {
  test(`Nombre inválido rechazado al crear y actualizar: ${label}`, async () => {
    const reference = doc(database(), 'users', 'ana');
    await assertFails(setDoc(reference, profile({ display_name: name })));
    await setDoc(reference, profile());
    await assertFails(updateDoc(reference, { display_name: name }));
  });
}

test('Nombre: rechaza espacios exteriores Unicode que JavaScript trim() elimina', async () => {
  const reference = doc(database(), 'users', 'ana');
  await setDoc(reference, profile());
  const whitespace = [
    '\u00a0', '\u1680', '\u2000', '\u2001', '\u2002', '\u2003', '\u2004',
    '\u2005', '\u2006', '\u2007', '\u2008', '\u2009', '\u200a',
    '\u2028', '\u2029', '\u202f', '\u205f', '\u3000', '\ufeff',
  ];
  for (const space of whitespace) {
    for (const name of [space, `${space}Ana`, `Ana${space}`]) {
      assert.notEqual(name, name.trim());
      await assertFails(updateDoc(reference, { display_name: name }));
    }
  }
});

test('Migración: una app anterior crea perfil sin nombre y actualiza preferencias', async () => {
  const reference = doc(database(), 'users', 'ana');
  const legacy = profile();
  delete legacy.display_name;
  await assertSucceeds(setDoc(reference, legacy));
  // Same merge operation used by updatePreferences() in the existing app.
  await assertSucceeds(setDoc(reference, {
    preferences: { ...legacy.preferences, gps_enabled: true },
  }, { merge: true }));
  assert.equal((await getDoc(reference)).data().display_name, undefined);
});

test('Migración: completar el nombre conserva fecha y preferencias', async () => {
  const reference = doc(database(), 'users', 'ana');
  const legacy = profile();
  delete legacy.display_name;
  await setDoc(reference, legacy);
  const before = (await getDoc(reference)).data();
  await assertSucceeds(updateDoc(reference, { display_name: 'Ana María' }));
  const after = (await getDoc(reference)).data();
  assert.equal(after.display_name, 'Ana María');
  assert.ok(before.created_at.isEqual(after.created_at));
  assert.deepEqual(before.preferences, after.preferences);
});

test('Perfil: se puede cambiar el nombre y combinar preferencias sin perderlo', async () => {
  const reference = doc(database(), 'users', 'ana');
  await setDoc(reference, profile());
  await assertSucceeds(updateDoc(reference, { display_name: 'Ana María' }));
  await assertSucceeds(setDoc(reference, {
    preferences: { ...profile().preferences, notifications_enabled: true },
  }, { merge: true }));
  assert.equal((await getDoc(reference)).data().display_name, 'Ana María');
});

test('Perfil: un nombre guardado no se elimina ni se pierde al reemplazar el documento', async () => {
  const reference = doc(database(), 'users', 'ana');
  await setDoc(reference, profile());
  await assertFails(updateDoc(reference, { display_name: deleteField() }));
  const replacement = (await getDoc(reference)).data();
  delete replacement.display_name;
  await assertFails(setDoc(reference, replacement));
});

test('Perfil: ni el propietario puede eliminar el documento', async () => {
  const reference = doc(database(), 'users', 'ana');
  await setDoc(reference, profile());
  await assertFails(deleteDoc(reference));
});

test('Fecha: creación exige timestamp del servidor y no una fecha inventada', async () => {
  const reference = doc(database(), 'users', 'ana');
  await assertFails(setDoc(reference, profile({ created_at: Timestamp.fromMillis(0) })));
  await assertFails(setDoc(reference, profile({ created_at: '2026-10-17' })));
});

test('Fecha: created_at no puede cambiar ni eliminarse', async () => {
  const reference = doc(database(), 'users', 'ana');
  await setDoc(reference, profile());
  await assertFails(updateDoc(reference, { created_at: Timestamp.fromMillis(0) }));
  await assertFails(updateDoc(reference, { created_at: deleteField() }));
});

test('Correo: debe coincidir con el token tanto al crear como al actualizar', async () => {
  const reference = doc(database(), 'users', 'ana');
  for (const email of ['otra@example.com', null, 123]) {
    await assertFails(setDoc(reference, profile({ email })));
  }
  await setDoc(reference, profile());
  await assertFails(updateDoc(reference, { email: 'otra@example.com' }));
  await assertFails(updateDoc(reference, { email: deleteField() }));
});

test('Correo: un token sin email no permite crear el perfil', async () => {
  const db = environment.authenticatedContext('ana').firestore();
  await assertFails(setDoc(doc(db, 'users', 'ana'), profile()));
});

for (const field of ['email', 'created_at', 'preferences']) {
  test(`Perfil: campo obligatorio ausente rechazado: ${field}`, async () => {
    const value = profile();
    delete value[field];
    await assertFails(setDoc(doc(database(), 'users', 'ana'), value));
  });
}

test('Perfil: se rechazan campos extra al crear y actualizar', async () => {
  const reference = doc(database(), 'users', 'ana');
  for (const field of ['admin', 'uid', 'password', 'displayName']) {
    await assertFails(setDoc(reference, { ...profile(), [field]: 'no permitido' }));
  }
  await setDoc(reference, profile());
  await assertFails(updateDoc(reference, { admin: true }));
});

const invalidPreferences = [
  ['null', null], ['lista', []], ['texto', 'rio_consulado'],
  ['vacías', {}],
  ['estación distinta', { ...profile().preferences, preferred_station: 'otra' }],
  ['GPS no booleano', { ...profile().preferences, gps_enabled: 'true' }],
  ['notificaciones no booleanas', { ...profile().preferences, notifications_enabled: 1 }],
  ['campo extra', { ...profile().preferences, admin: true }],
  ...Object.keys(profile().preferences).map(field => {
    const value = { ...profile().preferences };
    delete value[field];
    return [`falta ${field}`, value];
  }),
];
for (const [label, preferences] of invalidPreferences) {
  test(`Preferencias inválidas rechazadas al crear y actualizar: ${label}`, async () => {
    const reference = doc(database(), 'users', 'ana');
    await assertFails(setDoc(reference, profile({ preferences })));
    await setDoc(reference, profile());
    await assertFails(updateDoc(reference, { preferences }));
  });
}

test('Perfil: no se puede listar la colección completa de usuarios', async () => {
  await assertFails(getDocs(collection(database(), 'users')));
});

for (const parent of ['query_history', 'feedback']) {
  const child = parent === 'query_history' ? 'queries' : 'entries';
  test(`${parent}: lectura propia permitida, lectura ajena/anónima y escrituras rechazadas`, async () => {
    const path = `${parent}/ana/${child}/query-001`;
    // Minimal fixtures test authorization, not backend document validation.
    await seed(path, { station_id: 'rio_consulado' });
    const ownReference = doc(database(), path);
    await assertSucceeds(getDoc(ownReference));
    await assertSucceeds(getDocs(collection(database(), parent, 'ana', child)));
    await assertFails(getDoc(doc(database('luis'), path)));
    await assertFails(getDocs(collection(database('luis'), parent, 'ana', child)));
    await assertFails(getDoc(doc(environment.unauthenticatedContext().firestore(), path)));
    await assertFails(setDoc(doc(database(), parent, 'ana', child, 'new-query'), {}));
    await assertFails(updateDoc(ownReference, { actual_min: 10 }));
    await assertFails(deleteDoc(ownReference));
  });
}

test('Alertas: lectura autenticada y consulta por estación; sin escrituras móviles', async () => {
  const path = 'alerts/alert-001';
  await seed(path, { station_id: 'rio_consulado', active: true });
  const db = database();
  await assertSucceeds(getDoc(doc(db, path)));
  const snapshot = await assertSucceeds(getDocs(query(collection(db, 'alerts'),
    where('station_id', '==', 'rio_consulado'), where('active', '==', true))));
  assert.equal(snapshot.size, 1);
  const anonymous = environment.unauthenticatedContext().firestore();
  await assertFails(getDoc(doc(anonymous, path)));
  await assertFails(getDocs(collection(anonymous, 'alerts')));
  await assertFails(setDoc(doc(db, 'alerts', 'new-alert'), {}));
  await assertFails(updateDoc(doc(db, path), { active: false }));
  await assertFails(deleteDoc(doc(db, path)));
});

test('Caché: lectura, listado, creación, actualización y borrado bloqueados al cliente', async () => {
  await seed('predictions/cache-001', { waiting_time_min: 12 });
  const db = database();
  const reference = doc(db, 'predictions', 'cache-001');
  await assertFails(getDoc(reference));
  await assertFails(getDocs(collection(db, 'predictions')));
  await assertFails(setDoc(doc(db, 'predictions', 'new-cache'), {}));
  await assertFails(updateDoc(reference, { waiting_time_min: 1 }));
  await assertFails(deleteDoc(reference));
});

test('Rutas no previstas y subcolecciones desconocidas permanecen bloqueadas', async () => {
  for (const path of ['unknown/document', 'users/ana/private/document']) {
    await assertFails(getDoc(doc(database(), path)));
    await assertFails(setDoc(doc(database(), path), {}));
  }
});

test('Plan de la app: crear y reintentar no duplica ni cambia la fecha del perfil', async () => {
  const db = database(); const reference = doc(db, 'users', 'ana');
  async function apply(name) {
    await runTransaction(db, async transaction => {
      const snapshot = await transaction.get(reference);
      const plan = planProfileWrite(snapshot.exists() ? snapshot.data() : undefined,
        'ana@example.com', name, profile().preferences, serverTimestamp());
      if (plan.kind === 'create') transaction.set(reference, plan.data);
      if (plan.kind === 'update') transaction.update(reference, plan.data);
    });
  }
  await apply('  María José  ');
  const before = (await getDoc(reference)).data();
  await apply('Nombre anterior de Auth');
  const after = (await getDoc(reference)).data();
  assert.equal(after.display_name, 'María José');
  assert.ok(before.created_at.isEqual(after.created_at));
  await environment.withSecurityRulesDisabled(async context => {
    assert.equal((await getDocs(collection(context.firestore(), 'users'))).size, 1);
  });
});

test('Plan de la app: migra un perfil anterior sin modificar preferencias o fecha', async () => {
  const db = database(); const reference = doc(db, 'users', 'ana');
  const legacy = profile(); delete legacy.display_name;
  legacy.preferences.gps_enabled = true;
  await setDoc(reference, legacy);
  const before = (await getDoc(reference)).data();
  await runTransaction(db, async transaction => {
    const snapshot = await transaction.get(reference);
    const plan = planProfileWrite(snapshot.data(), 'ana@example.com', ' Ana María ',
      profile().preferences, serverTimestamp());
    assert.equal(plan.kind, 'update');
    if (plan.kind === 'update') transaction.update(reference, plan.data);
  });
  const after = (await getDoc(reference)).data();
  assert.equal(after.display_name, 'Ana María');
  assert.deepEqual(before.preferences, after.preferences);
  assert.ok(before.created_at.isEqual(after.created_at));
});

test('Nombre ingresado en Configuración completa nombres anteriores vacíos o inválidos', async () => {
  const db = database(); const reference = doc(db, 'users', 'ana');
  const createdAt = Timestamp.fromMillis(1000);
  for (const invalid of ['', null, 123, '   ']) {
    await seed('users/ana', profile({ display_name: invalid, created_at: createdAt }));
    await runTransaction(db, async transaction => {
      const snapshot = await transaction.get(reference);
      const plan = planProfileWrite(snapshot.data(), 'ana@example.com', ' Ana María ',
        profile().preferences, serverTimestamp());
      assert.equal(plan.kind, 'update');
      if (plan.kind === 'update') transaction.update(reference, plan.data);
    });
    const saved = (await getDoc(reference)).data();
    assert.equal(saved.display_name, 'Ana María');
    assert.ok(saved.created_at.isEqual(createdAt));
  }
});
