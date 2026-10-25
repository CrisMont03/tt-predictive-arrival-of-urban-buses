import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';
import { validatePassword, type PasswordValidationStatus } from 'firebase/auth';
import { useEffect, useRef, useState } from 'react';
import { View, type TextInputInstance } from 'react-native';
import { AuthPage, Button, Field, Label, Notice, styles, usePalette } from '@/components/arribo-ui';
import { firebaseServices } from '@/services/firebase';
import { useArribo } from '@/state/arribo-provider';
import { passwordRecommendations, passwordRequirements, registrationErrors } from '@/utils/auth-validation';

export default function RegisterScreen() {
  const { mode, registerAccount } = useArribo(); const p = usePalette();
  const [name, setName] = useState(''); const [email, setEmail] = useState('');
  const [password, setPassword] = useState(''); const [confirmation, setConfirmation] = useState('');
  const [touched, setTouched] = useState({ name: false, email: false, password: false, confirmation: false });
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const [policy, setPolicy] = useState<PasswordValidationStatus | null>(null);
  const [policyPassword, setPolicyPassword] = useState<string | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const locked = useRef(false); const emailRef = useRef<TextInputInstance>(null); const passwordRef = useRef<TextInputInstance>(null); const confirmationRef = useRef<TextInputInstance>(null);
  const errors = registrationErrors(name, email, password, confirmation);
  const touch = (field: keyof typeof touched) => setTouched(value => ({ ...value, [field]: true }));
  useEffect(() => {
    if (mode !== 'live') return;
    let alive = true;
    const timer = setTimeout(() => {
      Promise.resolve().then(() => validatePassword(firebaseServices().auth, password)).then(status => {
        if (alive) { setPolicy(status); setPolicyPassword(password); setPolicyError(null); }
      }).catch(() => { if (alive) { setPolicy(null); setPolicyError('No pudimos consultar los requisitos. Se comprobarán al enviar.'); } });
    }, 350);
    return () => { alive = false; clearTimeout(timer); };
  }, [mode, password]);
  async function submit() {
    if (locked.current || mode !== 'live') return;
    setTouched({ name: true, email: true, password: true, confirmation: true }); setError(null);
    if (Object.values(errors).some(Boolean)) return;
    locked.current = true; setBusy(true);
    try {
      const status = await validatePassword(firebaseServices().auth, password); setPolicy(status); setPolicyPassword(password);
      if (!status.isValid) { setError('Tu contraseña debe cumplir los requisitos indicados.'); return; }
      await registerAccount(name.trim(), email.trim(), password);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo crear la cuenta.'); }
    finally { locked.current = false; setBusy(false); }
  }
  return <AuthPage title="Crea tu cuenta" subtitle="Un acceso personal para tus consultas y reportes.">
    {error && <Notice error>{error}</Notice>}
    {mode === 'demo' ? <Notice>La creación de cuentas está disponible en modo live. En demostración no guardamos estos datos en Firebase.</Notice> : <>
      <Field label="Nombre para mostrar" icon="person-outline" helper="Tu nombre o un apodo, hasta 100 caracteres y en una sola línea." placeholder="¿Cómo te llamas?" value={name} onChangeText={setName} error={touched.name ? errors.name : undefined} onBlur={() => touch('name')} autoCapitalize="words" autoComplete="name" textContentType="name" returnKeyType="next" onSubmitEditing={() => emailRef.current?.focus()} editable={!busy} />
      <Field ref={emailRef} label="Correo electrónico" icon="mail-outline" placeholder="nombre@correo.com" value={email} onChangeText={setEmail} error={touched.email ? errors.email : undefined} onBlur={() => touch('email')} autoCapitalize="none" autoCorrect={false} autoComplete="email" textContentType="emailAddress" keyboardType="email-address" returnKeyType="next" onSubmitEditing={() => passwordRef.current?.focus()} editable={!busy} />
      <Field ref={passwordRef} label="Contraseña" icon="lock-closed-outline" placeholder="Crea una contraseña" value={password} onChangeText={setPassword} error={touched.password ? errors.password : undefined} onBlur={() => touch('password')} secureTextEntry autoCapitalize="none" autoCorrect={false} autoComplete="new-password" textContentType="newPassword" returnKeyType="next" onSubmitEditing={() => confirmationRef.current?.focus()} editable={!busy} />
      <View style={styles.gap}><Label muted style={{ fontSize: 13 }}>Requisitos obligatorios:</Label>{policy ? passwordRequirements(policy).map(check => <View key={check.label} style={styles.row}><Ionicons name={check.met && touched.password && policyPassword === password ? 'checkmark-circle-outline' : 'ellipse-outline'} size={16} color={check.met && touched.password && policyPassword === password ? p.green : p.muted} /><Label muted style={{ fontSize: 13, lineHeight: 20 }}>{check.label}</Label></View>) : <Label muted style={{ fontSize: 13 }}>{policyError ?? 'Consultando la política de contraseña…'}</Label>}</View>
      {policy && passwordRecommendations(policy, password).length > 0 && <View style={styles.gap}><Label muted style={{ fontSize: 13 }}>Para una contraseña más fuerte:</Label>{passwordRecommendations(policy, password).map(check => <View key={check.label} style={styles.row}><Ionicons name={check.met && touched.password ? 'checkmark-circle-outline' : 'ellipse-outline'} size={16} color={check.met && touched.password ? p.green : p.muted} /><Label muted style={{ fontSize: 13, lineHeight: 20 }}>{check.label}</Label></View>)}</View>}
      <Field ref={confirmationRef} label="Confirmar contraseña" icon="shield-checkmark-outline" placeholder="Repite tu contraseña" value={confirmation} onChangeText={setConfirmation} error={touched.confirmation ? errors.confirmation : undefined} onBlur={() => touch('confirmation')} secureTextEntry autoCapitalize="none" autoCorrect={false} autoComplete="new-password" textContentType="newPassword" returnKeyType="go" onSubmitEditing={() => void submit()} editable={!busy} />
      <Button title="Crear cuenta" busyTitle="Creando tu cuenta…" icon="arrow-forward" busy={busy} onPress={() => void submit()} />
      <Label muted style={{ fontSize: 12, lineHeight: 20 }}>GPS y notificaciones son opcionales.</Label>
    </>}
    <View style={[styles.divider, { borderColor: p.border }]}><Label muted>¿Ya tienes cuenta?</Label><Button title="Iniciar sesión" secondary disabled={busy} onPress={() => router.replace('/(auth)')} /></View>
  </AuthPage>;
}
