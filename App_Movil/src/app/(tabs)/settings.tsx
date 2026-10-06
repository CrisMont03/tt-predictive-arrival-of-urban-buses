import * as Location from 'expo-location';
import { useState } from 'react';
import { Switch, View } from 'react-native';
import { Button, Card, Label, Notice, Page, styles, usePalette } from '@/components/arribo-ui';
import { STATION } from '@/config/arribo';
import { useArribo } from '@/state/arribo-provider';
import type { DemoScenario } from '@/types/arribo';

export default function SettingsScreen() {
  const { session, mode, preferences, updatePreferences, logout, scenario, setScenario, pendingCount, retryPending } = useArribo(); const p = usePalette();
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  async function run(task: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await task(); } catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo guardar.'); } finally { setBusy(false); }
  }
  async function setGPS(enabled: boolean) {
    if (enabled && !(await Location.requestForegroundPermissionsAsync()).granted) throw new Error('No se concedió ubicación. Puedes seguir consultando la espera.');
    await updatePreferences({ gps_enabled: enabled });
  }
  return <Page title="A tu manera" subtitle="Preferencias para tus próximas consultas.">
    {error && <Notice error>{error}</Notice>}
    <Card><Label style={styles.cardTitle}>Tu cuenta</Label><Label muted>{session?.demo ? 'Sesión de demostración' : session?.email}</Label></Card>
    <Card><Label style={styles.cardTitle}>Estación preferida</Label><Label>{STATION.name}</Label><Label muted>Por ahora, ARRIBO está disponible en esta estación.</Label></Card>
    <Card><View style={styles.between}><View style={styles.flex}><Label style={styles.cardTitle}>Ubicación opcional</Label><Label muted>Avisa si estás a más de 200 metros de la parada.</Label></View><Switch accessibilityLabel="Usar ubicación opcional" value={preferences.gps_enabled} disabled={busy} trackColor={{ true: p.blue }} onValueChange={enabled => void run(() => setGPS(enabled))} /></View><Label muted>Tu ubicación no modifica la predicción.</Label></Card>
    <Card><Label style={styles.cardTitle}>Notificaciones</Label><Label muted>Los avisos están disponibles en Alertas. Las notificaciones con la app cerrada se habilitarán cuando esté configurada la versión instalable.</Label><Switch accessibilityLabel="Notificaciones push aún no disponibles" value={false} disabled /></Card>
    {pendingCount > 0 && <Card><Label>{pendingCount} reportes pendientes de envío</Label><Button secondary title="Reintentar envío" busy={busy} onPress={() => void run(retryPending)} /></Card>}
    {mode === 'demo' && <Card><Label style={styles.cardTitle}>Escenario de demostración</Label>{([['normal', 'Respuesta normal'], ['slow', 'Respuesta lenta'], ['error', 'Error de servicio'], ['empty', 'Historial y alertas vacíos']] as [DemoScenario, string][]).map(([value, name]) => <Button key={value} title={`${scenario === value ? '✓ ' : ''}${name}`} secondary={scenario !== value} onPress={() => setScenario(value)} />)}</Card>}
    <Button secondary title="Cerrar sesión" busy={busy} onPress={() => void run(logout)} />
  </Page>;
}
