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
