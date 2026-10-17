import { router } from 'expo-router';
import { useRef, useState } from 'react';
import { AuthPage, Button, Field, Notice } from '@/components/arribo-ui';
import { useArribo } from '@/state/arribo-provider';
import { emailError } from '@/utils/auth-validation';

export default function RecoverScreen() {
  const { resetPassword, mode } = useArribo();
  const [email, setEmail] = useState(''); const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [sent, setSent] = useState(false);
  const locked = useRef(false); const invalid = emailError(email);
  async function send() {
    if (locked.current || mode !== 'live') return;
    setTouched(true); setError(null); if (invalid) return;
    locked.current = true; setBusy(true);
    try { await resetPassword(email.trim()); setSent(true); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo enviar la solicitud.'); }
    finally { locked.current = false; setBusy(false); }
  }
  return <AuthPage title="Recupera tu acceso" subtitle="Te ayudamos a volver a tu cuenta.">
    {error && <Notice error>{error}</Notice>}
    {mode === 'demo' ? <Notice>La recuperación está disponible para cuentas reales, en modo live.</Notice> : sent ? <Notice>Si el correo tiene una cuenta, recibirás instrucciones para recuperar la contraseña. Revisa también tu carpeta de spam.</Notice> : <>
      <Field label="Correo electrónico" icon="mail-outline" placeholder="nombre@correo.com" value={email} onChangeText={setEmail} onBlur={() => setTouched(true)} error={touched ? invalid : undefined} autoCapitalize="none" autoCorrect={false} autoComplete="email" keyboardType="email-address" returnKeyType="send" onSubmitEditing={() => void send()} editable={!busy} />
      <Button title="Enviar instrucciones" busyTitle="Enviando…" busy={busy} onPress={() => void send()} />
    </>}
    <Button secondary title="Volver a iniciar sesión" disabled={busy} onPress={() => router.replace('/(auth)')} />
  </AuthPage>;
}
