import { Ionicons } from '@expo/vector-icons';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform } from 'react-native';
import { Button, Card, Field, Label, Notice, Page, styles, usePalette } from '@/components/arribo-ui';
import { STATION } from '@/config/arribo';
import { useArribo } from '@/state/arribo-provider';

export default function AccessScreen() {
  const { mode, enterDemo, login, resetPassword } = useArribo(); const p = usePalette();
  const [email, setEmail] = useState(''); const [password, setPassword] = useState('');
  const [register, setRegister] = useState(false); const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null); const [message, setMessage] = useState<string | null>(null);
  async function run(task: () => Promise<void>) {
    setBusy(true); setError(null); setMessage(null);
    try { await task(); } catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo acceder.'); } finally { setBusy(false); }
  }
  return (
    <KeyboardAvoidingView style={styles.page} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <Page title="Tu próxima llegada" subtitle={`Menos incertidumbre al esperar en ${STATION.name}.`}>
        <Card><Ionicons name="bus-outline" color={p.blue} size={48} /><Label style={styles.cardTitle}>Planea tu viaje con más información</Label><Label muted>Consulta una estimación de espera, revisa tus consultas y recibe avisos del servicio.</Label></Card>
        {error && <Notice error>{error}</Notice>}{message && <Notice>{message}</Notice>}
        {mode === 'demo' ? <><Button title="Explorar demostración" busy={busy} onPress={() => void run(enterDemo)} /><Label muted style={styles.center}>Puedes probar todas las pantallas sin crear una cuenta.</Label></> :
          <Card>
            <Label style={styles.cardTitle}>{register ? 'Crea tu cuenta' : 'Inicia sesión'}</Label>
            <Field label="Correo electrónico" value={email} onChangeText={setEmail} autoCapitalize="none" autoComplete="email" keyboardType="email-address" />
            <Field label="Contraseña" value={password} onChangeText={setPassword} secureTextEntry autoComplete={register ? 'new-password' : 'current-password'} />
            <Button title={register ? 'Crear cuenta' : 'Entrar'} busy={busy} disabled={!email.trim() || password.length < 6} onPress={() => void run(() => login(email, password, register))} />
            <Button secondary title={register ? 'Ya tengo una cuenta' : 'Crear una cuenta'} disabled={busy} onPress={() => { setRegister(!register); setError(null); }} />
            <Button secondary title="Recuperar contraseña" disabled={busy || !email.trim()} onPress={() => void run(async () => { await resetPassword(email); setMessage('Si el correo tiene una cuenta, recibirás instrucciones para recuperar la contraseña.'); })} />
          </Card>}
      </Page>
    </KeyboardAvoidingView>
  );
}
