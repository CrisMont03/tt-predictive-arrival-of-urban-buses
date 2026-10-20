import type { PasswordValidationStatus } from 'firebase/auth';
import { displayNameError } from './profile-name.ts';

export function emailError(email: string): string | undefined {
  if (!email.trim()) return 'Ingresa tu correo electrónico.';
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) return 'Escribe un correo válido, como nombre@correo.com.';
}
export function loginErrors(email: string, password: string) {
  return { email: emailError(email), password: password.length ? undefined : 'Ingresa tu contraseña.' };
}
export function registrationErrors(name: string, email: string, password: string, confirmation: string) {
  return {
    name: displayNameError(name), email: emailError(email),
    password: password.length ? undefined : 'Crea una contraseña.',
    confirmation: !confirmation.length ? 'Confirma tu contraseña.' : confirmation !== password ? 'Las contraseñas no coinciden.' : undefined,
  };
}
export function passwordRequirements(status: PasswordValidationStatus): { label: string; met: boolean }[] {
  const options = status.passwordPolicy.customStrengthOptions;
  const checks: { label: string; met: boolean | undefined }[] = [
    { label: `Al menos ${options.minPasswordLength ?? 6} caracteres`, met: status.meetsMinPasswordLength },
    { label: 'Una letra minúscula', met: status.containsLowercaseLetter }, { label: 'Una letra mayúscula', met: status.containsUppercaseLetter },
    { label: 'Un número', met: status.containsNumericCharacter }, { label: 'Un carácter especial', met: status.containsNonAlphanumericCharacter },
  ];
  return checks.filter((check): check is { label: string; met: boolean } => check.met !== undefined);
}

// Suggestions must never be presented as requirements not configured in Firebase.
export function passwordRecommendations(status: PasswordValidationStatus, password: string): { label: string; met: boolean }[] {
  const checks: { label: string; met: boolean; required: boolean }[] = [
    { label: 'Una letra mayúscula', met: /[A-Z]/.test(password), required: status.containsUppercaseLetter !== undefined },
    { label: 'Un número', met: /[0-9]/.test(password), required: status.containsNumericCharacter !== undefined },
    { label: 'Un carácter especial', met: Array.from(password).some(value => status.passwordPolicy.allowedNonAlphanumericCharacters?.includes(value)), required: status.containsNonAlphanumericCharacter !== undefined },
  ];
  return checks.filter(check => !check.required).map(({ label, met }) => ({ label, met }));
}
