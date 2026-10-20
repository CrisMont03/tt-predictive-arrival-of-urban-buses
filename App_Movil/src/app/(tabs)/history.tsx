import { router } from 'expo-router';
import { useRef, useState } from 'react';
import { View } from 'react-native';
import { Badge, Button, Card, Empty, IconBadge, Label, Loading, Notice, Page, styles, usePalette } from '@/components/arribo-ui';
import { useArribo } from '@/state/arribo-provider';
import { stationName } from '@/data/stations';
import { averageByHour, formatDate } from '@/utils/arribo';

export default function HistoryScreen() {
  const { history, scenario, mode, refreshHistory, hasMore, dataReady, historyLoading } = useArribo(); const p = usePalette();
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const locked = useRef(false);
  const entries = mode === 'demo' && scenario === 'empty' ? [] : history;
  const bars = averageByHour(entries); const max = Math.max(1, ...bars.map(bar => bar.average));
  async function load(more: boolean) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(null);
    try { await refreshHistory(more); } catch { setError('No se pudo actualizar el historial. Puedes reintentar; tus consultas guardadas no se han eliminado.'); }
    finally { locked.current = false; setBusy(false); }
  }
  return <Page title="Tus consultas" subtitle="Predicciones y tiempos que has reportado.">
    {error && <Notice error>{error}</Notice>}
    {!dataReady || (historyLoading && entries.length === 0) ? <Loading title="Cargando tus consultas…" /> : entries.length === 0 ? <Empty icon="time-outline" tone="green" title="Tu historial empieza aquí" description="Cuando consultes una predicción, aparecerá aquí. Después podrás reportar cuánto esperaste." /> : <>
      <Card><View style={styles.between}><Label style={styles.cardTitle}>Predicción promedio por hora</Label><Badge tone="violet">{entries.length} consultas</Badge></View><Label muted>Promedio de predicciones por hora. No representa tus esperas reales.</Label>
        {bars.map(bar => <View key={bar.hour} accessible accessibilityLabel={`${bar.hour} horas: ${bar.average.toFixed(1)} minutos, ${bar.count} consultas`} style={styles.gap}><View style={styles.between}><Label>{bar.hour}:00</Label><Label muted>{bar.average.toFixed(1)} min · {bar.count}</Label></View><View style={{ height: 8, borderRadius: 8, backgroundColor: p.soft }}><View style={{ width: `${bar.average / max * 100}%`, height: 8, borderRadius: 8, backgroundColor: p.blue }} /></View></View>)}
      </Card>
      <Label accessibilityRole="header" style={styles.cardTitle}>Consultas recientes</Label>
      {entries.map(entry => <Card key={entry.query_id}>
        <View style={styles.between}><View style={styles.headingRow}><IconBadge name="time-outline" tone="violet" size={20} /><Label style={[styles.cardTitle, styles.flex]}>{entry.waiting_time_min.toFixed(1)} min estimados</Label></View><Badge tone={entry.simulation ? 'muted' : 'blue'}>{entry.simulation ? 'Demostración' : 'Estimación'}</Badge></View>
        <Label muted>{stationName(entry.station_id)}</Label><Label muted>{formatDate(entry.requested_at ?? entry.timestamp)}</Label>
        {mode === 'live' && entry.simulation && entry.query_id.startsWith('arribo-c345-') && <Label muted selectable>Prueba: {entry.query_id}</Label>}
        {entry.actual_min !== undefined ? <><Label style={{ color: p.green, fontWeight: '600' }}>Espera reportada: {entry.actual_min} min</Label><Badge tone={entry.feedback_pending ? 'warning' : 'green'}>{entry.feedback_pending ? 'Pendiente de envío' : mode === 'demo' ? 'Reporte demo local' : entry.simulation ? 'Reporte simulado enviado' : 'Reporte enviado'}</Badge></> : <Button secondary title="Reportar espera real" icon="chatbubble-outline" onPress={() => router.push({ pathname: '/feedback', params: { queryId: entry.query_id } })} />}
      </Card>)}
    </>}
    {mode === 'live' && <Button secondary title={hasMore ? 'Cargar más consultas' : 'Actualizar historial'} busyTitle="Actualizando…" busy={busy || historyLoading} onPress={() => void load(hasMore)} />}
  </Page>;
}
