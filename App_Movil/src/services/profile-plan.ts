import type { Preferences } from '../types/arribo';
import { normalizedProfileName } from '../utils/profile-name.ts';

type StoredProfile = { display_name?: unknown };
type Plan<T> =
  | { kind: 'create'; data: { email: string; created_at: T; preferences: Preferences; display_name?: string } }
  | { kind: 'update'; data: { display_name: string } }
  | { kind: 'none' };

// Pure decision shared by the Firestore transaction and emulator/unit tests.
// Existing names are never replaced during automatic migration.
export function planProfileWrite<T>(
  existing: StoredProfile | undefined,
  email: string | null,
  name: string | null | undefined,
  preferences: Preferences,
  createdAt: T,
): Plan<T> {
  const normalized = normalizedProfileName(name);
  if (existing) {
    if (normalizedProfileName(existing.display_name) || !normalized) return { kind: 'none' };
    return { kind: 'update', data: { display_name: normalized } };
  }
  if (!email) throw new Error('La cuenta no tiene correo. No se pudo crear su perfil.');
  return { kind: 'create', data: {
    email, created_at: createdAt, preferences: { ...preferences },
    ...(normalized ? { display_name: normalized } : {}),
  } };
}
