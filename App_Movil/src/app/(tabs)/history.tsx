import { router } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';
import { Button, Card, Empty, Label, Notice, Page, styles, usePalette } from '@/components/arribo-ui';
import { useArribo } from '@/state/arribo-provider';
import { averageByHour, formatDate } from '@/utils/arribo';

export default function HistoryScreen() {
  const { history, scenario, mode, refreshHistory, hasMore } = useArribo(); const p = usePalette();
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const entries = mode === 'demo' && scenario === 'empty' ? [] : history;
  const bars = averageByHour(entries); const max = Math.max(1, ...bars.map(bar => bar.average));
  async function load(more: boolean) {
    setBusy(true); setError(null);
    try { await refreshHistory(more); } catch { setError('No se pudo actualizar el historial.'); } finally { setBusy(false); }
  }
  return <Page title="Tus consultas" subtitle="Predicciones y tiempos que has reportado.">
    {error && <Notice error>{error}</Notice>}
    {entries.length === 0 ? <Empty icon="time-outline" title="Tu historial empieza aquí" description="Cuando consultes una predicción, aparecerá en esta pantalla." /> : <>
      <Card><Label style={styles.cardTitle}>Predicción promedio por hora</Label><Label muted>Calculada con las {entries.length} consultas cargadas, no con esperas reales.</Label>
        {bars.map(bar => <View key={bar.hour} accessible accessibilityLabel={`${bar.hour} horas: ${bar.average.toFixed(1)} minutos, ${bar.count} consultas`} style={styles.gap}><View style={styles.between}><Label>{bar.hour}:00</Label><Label muted>{bar.average.toFixed(1)} min · {bar.count} consultas</Label></View><View style={{ height: 10, borderRadius: 5, backgroundColor: p.border }}><View style={{ width: `${bar.average / max * 100}%`, height: 10, borderRadius: 5, backgroundColor: p.blue }} /></View></View>)}
      </Card>
      {entries.map(entry => <Card key={entry.query_id}><View style={styles.between}><Label style={styles.cardTitle}>{entry.waiting_time_min.toFixed(1)} min estimados</Label><Label muted>{entry.simulation ? 'Ejemplo' : 'Consulta'}</Label></View><Label muted>{formatDate(entry.requested_at ?? entry.timestamp)}</Label>{entry.actual_min !== undefined ? <Label style={{ color: p.green }}>Espera reportada: {entry.actual_min} min{entry.feedback_pending ? ' · pendiente de envío' : ''}</Label> : <Button secondary title="Reportar espera real" onPress={() => router.push({ pathname: '/feedback', params: { queryId: entry.query_id } })} />}</Card>)}
    </>}
    {mode === 'live' && <Button secondary title={hasMore ? 'Cargar más consultas' : 'Actualizar historial'} busy={busy} onPress={() => void load(hasMore)} />}
  </Page>;
}
