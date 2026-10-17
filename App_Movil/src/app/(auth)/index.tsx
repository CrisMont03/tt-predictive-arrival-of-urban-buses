import { router } from 'expo-router';
import { useRef, useState } from 'react';
import { TextInput, View } from 'react-native';
import { AuthPage, Button, Field, Label, Notice, styles, TextAction, usePalette } from '@/components/arribo-ui';
import { useArribo } from '@/state/arribo-provider';
import { loginErrors } from '@/utils/auth-validation';

export default function LoginScreen() {
  const { mode, enterDemo, login } = useArribo(); const p = usePalette();
  const [email, setEmail] = useState(''); const [password, setPassword] = useState('');
  const [touched, setTouched] = useState({ email: false, password: false });
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const locked = useRef(false); const passwordRef = useRef<TextInput>(null);
  const errors = loginErrors(email, password);
  async function submit() {
    if (locked.current) return;
    setTouched({ email: true, password: true }); setError(null);
    if (mode === 'live' && Object.values(errors).some(Boolean)) return;
    locked.current = true; setBusy(true);
    try { if (mode === 'demo') await enterDemo(); else await login(email, password); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo iniciar sesión.'); }
    finally { locked.current = false; setBusy(false); }
  }
  return <AuthPage title={mode === 'demo' ? 'Tu próxima llegada' : 'Inicia sesión'} subtitle={mode === 'demo' ? 'Explora ARRIBO con datos de ejemplo, sin crear una cuenta.' : 'Menos incertidumbre al esperar...'}>
    {error && <Notice error>{error}</Notice>}
    {mode === 'demo' ? <><Notice>Modo demostración. No se crean cuentas ni se envían datos a Firebase.</Notice><Button title="Explorar demostración" icon="arrow-forward" busy={busy} onPress={() => void submit()} /></> : <>
      <Field label="Correo electrónico" icon="mail-outline" placeholder="nombre@correo.com" value={email} onChangeText={setEmail} error={touched.email ? errors.email : undefined} onBlur={() => setTouched(value => ({ ...value, email: true }))} autoCapitalize="none" autoCorrect={false} autoComplete="email" textContentType="emailAddress" keyboardType="email-address" returnKeyType="next" onSubmitEditing={() => passwordRef.current?.focus()} editable={!busy} />
      <Field ref={passwordRef} label="Contraseña" icon="lock-closed-outline" placeholder="Tu contraseña" value={password} onChangeText={setPassword} error={touched.password ? errors.password : undefined} onBlur={() => setTouched(value => ({ ...value, password: true }))} secureTextEntry autoCapitalize="none" autoCorrect={false} autoComplete="current-password" textContentType="password" returnKeyType="go" onSubmitEditing={() => void submit()} editable={!busy} />
      <TextAction title="Olvidé mi contraseña" disabled={busy} onPress={() => router.push('/(auth)/recover')} />
      <Button title="Iniciar sesión" busyTitle="Iniciando sesión…" icon="arrow-forward" busy={busy} onPress={() => void submit()} />
    </>}
    <View style={[styles.divider, { borderColor: p.border }]}><Label muted>¿No tienes cuenta?</Label><Button title="Crear cuenta" secondary disabled={busy} onPress={() => router.push('/(auth)/register')} /></View>
    <Label muted style={{ fontSize: 12, lineHeight: 20 }}>Tu ubicación es opcional. ARRIBO no rastrea el autobús por GPS.</Label>
  </AuthPage>;
}
