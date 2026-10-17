import * as Location from 'expo-location';
import { useRef, useState } from 'react';
import { Switch, View } from 'react-native';
import { Badge, Button, Card, IconBadge, Label, Notice, Page, styles, usePalette } from '@/components/arribo-ui';
import { initialStation } from '@/data/stations';
import { useArribo } from '@/state/arribo-provider';
import type { DemoScenario } from '@/types/arribo';

export default function SettingsScreen() {
  const { session, mode, preferences, updatePreferences, logout, scenario, setScenario, pendingCount, retryPending, profilePending, profileSyncing, retryProfile, online } = useArribo(); const p = usePalette();
  const favorite = initialStation(preferences.preferred_station, mode);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const locked = useRef(false);
  async function run(task: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true); setError(null);
    try { await task(); } catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo guardar.'); } finally { locked.current = false; setBusy(false); }
  }
  async function setGPS(enabled: boolean) {
    if (enabled && !(await Location.requestForegroundPermissionsAsync()).granted) throw new Error('No se concedió ubicación. Puedes seguir consultando la espera.');
    await updatePreferences({ gps_enabled: enabled });
  }
  return <Page title="A tu manera" subtitle="Preferencias para tus próximas consultas.">
    {error && <Notice error>{error}</Notice>}
    <Card><View style={styles.row}><IconBadge name="person-outline" tone="violet" /><View style={styles.flex}><Label style={styles.cardTitle}>{session?.displayName || 'Tu cuenta'}</Label><Label muted>{session?.demo ? 'Sesión de demostración' : session?.email}</Label></View></View><Badge tone={session?.demo ? 'muted' : 'blue'}>{session?.demo ? 'Datos de ejemplo' : 'Sesión Firebase Auth'}</Badge></Card>
    {!session?.demo && (profilePending || profileSyncing) && <Card><Label style={styles.cardTitle}>Sincronización del perfil</Label><Label muted>{profileSyncing ? 'Comprobando el nombre de tu perfil…' : 'Tu cuenta ya existe. Puedes reintentar guardar el nombre sin volver a registrarte.'}</Label><Button secondary title="Reintentar sincronización" busyTitle="Sincronizando…" busy={profileSyncing || busy} disabled={!online} onPress={() => void run(retryProfile)} /></Card>}
    <Card><View style={styles.row}><IconBadge name="location-outline" /><Label style={[styles.cardTitle, styles.flex]}>Estación preferida</Label></View><Label>{favorite.name}</Label><Label muted>La selección de una consulta no cambia tu estación favorita.</Label></Card>
    <Card><View style={styles.between}><View style={[styles.row, styles.flex]}><IconBadge name="navigate-outline" tone="green" /><View style={styles.flex}><Label style={styles.cardTitle}>Ubicación opcional</Label><Label muted>Avisa si estás a más de 200 metros de la parada.</Label></View></View><Switch accessibilityLabel="Usar ubicación opcional" value={preferences.gps_enabled} disabled={busy} trackColor={{ true: p.blue }} onValueChange={enabled => void run(() => setGPS(enabled))} /></View><Label muted>Tu ubicación no modifica la predicción.</Label></Card>
    <Card><View style={styles.between}><View style={styles.headingRow}><IconBadge name="notifications-outline" tone="accent" /><Label style={[styles.cardTitle, styles.flex]}>Notificaciones</Label></View><Badge tone="muted">Próximamente</Badge></View><Label muted>Los avisos están disponibles en Alertas. Las notificaciones con la app cerrada se habilitarán cuando esté configurada la versión instalable.</Label><Switch accessibilityLabel="Notificaciones push aún no disponibles" value={false} disabled /></Card>
    {pendingCount > 0 && <Card><Label>{pendingCount} reportes pendientes de envío</Label><Button secondary title="Reintentar envío" busy={busy} onPress={() => void run(retryPending)} /></Card>}
    {mode === 'demo' && <Card><Label style={styles.cardTitle}>Escenario de demostración</Label>{([['normal', 'Respuesta normal'], ['slow', 'Respuesta lenta'], ['error', 'Error de servicio'], ['empty', 'Historial y alertas vacíos']] as [DemoScenario, string][]).map(([value, name]) => <Button key={value} title={`${scenario === value ? '✓ ' : ''}${name}`} secondary={scenario !== value} onPress={() => setScenario(value)} />)}</Card>}
    <Button secondary title="Cerrar sesión" icon="log-out-outline" busy={busy} onPress={() => void run(logout)} />
  </Page>;
}
