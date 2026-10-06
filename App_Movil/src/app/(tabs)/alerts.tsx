import { Ionicons } from '@expo/vector-icons';
import { useEffect, useState } from 'react';
import { View } from 'react-native';
import { Card, Empty, Label, Page, styles, usePalette } from '@/components/arribo-ui';
import { useArribo } from '@/state/arribo-provider';
import { formatDate } from '@/utils/arribo';

export default function AlertsScreen() {
  const { alerts, scenario, mode } = useArribo(); const p = usePalette(); const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 15000); return () => clearInterval(timer); }, []);
  const rank = { info: 0, warning: 1, critical: 2 };
  const active = (mode === 'demo' && scenario === 'empty' ? [] : alerts).filter(alert => alert.active && Date.parse(alert.expires_at) > now).sort((a, b) => rank[b.severity] - rank[a.severity]);
  return <Page title="Avisos del servicio" subtitle="Información para tu próxima salida.">{active.length === 0 ? <Empty icon="checkmark-circle-outline" title="No hay avisos activos" description="Esto indica que no hay avisos publicados; no confirma el estado del servicio." /> : active.map(alert => <Card key={alert.id}><View style={styles.row}><Ionicons name={alert.severity === 'info' ? 'information-circle-outline' : 'warning-outline'} color={alert.severity === 'info' ? p.blue : p.danger} size={25} /><Label style={styles.cardTitle}>{alert.severity === 'critical' ? 'Aviso importante' : alert.severity === 'warning' ? 'Precaución' : 'Información'}</Label></View><Label>{alert.message}</Label><Label muted>Publicado: {formatDate(alert.created_at)}</Label><Label muted>Vigente hasta: {formatDate(alert.expires_at)}</Label></Card>)}</Page>;
}
