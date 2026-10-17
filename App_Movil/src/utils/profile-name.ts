export function displayNameError(value: string): string | undefined {
  const name = value.trim();
  if (!name) return 'Ingresa el nombre que quieres mostrar.';
  // Same UTF-16 length observed in Firestore Rules, including surrogate pairs.
  if (name.length > 100) return 'Usa un nombre de hasta 100 caracteres.';
  if (/[\r\n\u2028\u2029]/u.test(name)) return 'Escribe el nombre en una sola línea.';
}

export function normalizedProfileName(value: unknown): string | undefined {
  return typeof value === 'string' && !displayNameError(value) ? value.trim() : undefined;
}
