import { useEffect, useState } from 'react';
import { View } from 'react-native';
import { Badge, Button, Card, Empty, IconBadge, Label, Loading, Notice, Page, styles, usePalette } from '@/components/arribo-ui';
import { useArribo } from '@/state/arribo-provider';
import { formatDate } from '@/utils/arribo';

export default function AlertsScreen() {
  const { alerts, scenario, mode, dataReady, alertsLoading, alertsError, retryAlerts, online, selectedStation } = useArribo();
  const p = usePalette(); const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 15000); return () => clearInterval(timer); }, []);
  const rank = { info: 0, warning: 1, critical: 2 };
  const active = (mode === 'demo' && scenario === 'empty' ? [] : alerts).filter(alert => alert.station_id === selectedStation.id && alert.active && Date.parse(alert.expires_at) > now).sort((a, b) => rank[b.severity] - rank[a.severity]);
  return <Page title="Avisos del servicio" subtitle={`Información para ${selectedStation.name}.`}>
    {alertsError && <><Notice error>No se pudieron actualizar los avisos. {alertsError}</Notice><Button title="Reintentar lectura" secondary disabled={!online} busy={alertsLoading} onPress={retryAlerts} /></>}
    {!dataReady || (alertsLoading && active.length === 0) ? <Loading title="Consultando los avisos…" /> : active.length === 0 ? <Empty icon={alertsError ? 'cloud-offline-outline' : 'notifications-outline'} title={alertsError ? 'Información no disponible' : 'Sin avisos publicados'} description={alertsError ? 'No podemos confirmar si hay avisos nuevos. Vuelve a intentarlo cuando tengas conexión.' : 'No hay avisos activos en la información disponible. Esto no confirma que el servicio esté operando normalmente.'} /> : active.map(alert => <Card key={alert.id}>
      <View style={styles.between}><View style={styles.headingRow}><IconBadge name={alert.severity === 'info' ? 'information-circle-outline' : 'warning-outline'} tone={alert.severity === 'info' ? 'blue' : alert.severity === 'critical' ? 'danger' : 'warning'} /><Label style={[styles.cardTitle, styles.flex]}>{alert.severity === 'critical' ? 'Aviso importante' : alert.severity === 'warning' ? 'Precaución' : 'Información'}</Label></View><Badge tone={alert.severity === 'info' ? 'blue' : alert.severity === 'critical' ? 'danger' : 'warning'}>{alert.severity === 'critical' ? 'Prioridad alta' : 'Aviso de servicio'}</Badge></View>
      <Label>{alert.message}</Label><View style={[styles.divider, { borderColor: p.border }]}><Label muted style={{ fontSize: 13 }}>Publicado: {formatDate(alert.created_at)}</Label><Label muted style={{ fontSize: 13 }}>Vigente hasta: {formatDate(alert.expires_at)}</Label></View>
    </Card>)}
  </Page>;
}
