import { router, useLocalSearchParams } from 'expo-router';
import { useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, View } from 'react-native';
import { Badge, Button, Card, Field, IconBadge, Label, Notice, Page, styles } from '@/components/arribo-ui';
import { useArribo } from '@/state/arribo-provider';
import { stationName } from '@/data/stations';
import { formatDate, parseMinutes } from '@/utils/arribo';

export default function FeedbackScreen() {
  const { queryId } = useLocalSearchParams<{ queryId: string }>(); const { history, feedback, mode } = useArribo();
  const entry = history.find(item => item.query_id === queryId);
  const [input, setInput] = useState(''); const [sinceQuery, setSinceQuery] = useState(false); const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null); const [confirmation, setConfirmation] = useState<string | null>(null);
  const maximum = entry?.prediction_target === 'next_bus_wait' ? 1440 : 180;
  const minutes = parseMinutes(input, maximum);
  const locked = useRef(false); const [touched, setTouched] = useState(false);
  async function send() {
    if (locked.current || !entry || minutes === null) return;
    locked.current = true;
    setBusy(true); setError(null);
    try { const result = await feedback(entry.query_id, minutes, sinceQuery ? 'since_query' : 'unspecified'); setConfirmation(result === 'queued' ? 'Tu reporte está guardado y se enviará cuando haya conexión.' : mode === 'demo' ? 'Reporte guardado en la demostración local.' : entry.simulation ? 'Reporte simulado recibido por el servidor.' : 'Gracias. Tu reporte fue recibido.'); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo enviar.'); } finally { locked.current = false; setBusy(false); }
  }
  return <KeyboardAvoidingView style={styles.page} behavior={Platform.OS === 'ios' ? 'padding' : undefined}><Page title="¿Cuánto esperaste?" subtitle="Reporta al terminar tu espera. Es opcional.">
    {error && <Notice error>{error}</Notice>}
    {confirmation ? <><Notice>{confirmation}</Notice><Button title="Volver" onPress={() => router.back()} /></> : entry ? <Card>
      <View style={styles.row}><IconBadge name="chatbubble-ellipses-outline" /><Badge tone={entry.simulation ? 'muted' : 'accent'}>{entry.simulation ? 'Consulta de demostración' : 'Tu consulta'}</Badge></View><Label>Estación: {stationName(entry.station_id)}</Label><Label>Consulta: {formatDate(entry.requested_at ?? entry.timestamp)}</Label><Label muted>Estimación: {entry.waiting_time_min.toFixed(1)} minutos</Label>
      {mode === 'live' && entry.simulation && <Label muted selectable>Prueba: {entry.query_id}</Label>}
      {entry.actual_min !== undefined ? <Notice>Ya reportaste {entry.actual_min} minutos para esta consulta.{entry.feedback_pending ? ' Está pendiente de envío.' : ''}</Notice> : <><Label muted>Para identificar la llegada del bus, cuenta desde la hora de consulta mostrada hasta que llegó tu bus. Si ya esperabas antes, puedes reportar sin confirmar esta opción.</Label><Button secondary title={sinceQuery ? "Confirmado: conté desde esta consulta" : "Confirmar que conté desde esta consulta"} disabled={busy} onPress={() => setSinceQuery(value => !value)} /><Field label="Minutos de espera real" value={input} onChangeText={setInput} onBlur={() => setTouched(true)} error={touched && minutes === null ? `Ingresa un número entre 0 y ${maximum}, con hasta dos decimales.` : undefined} helper="Los retrasos largos también se conservan." editable={!busy} keyboardType="decimal-pad" placeholder="Por ejemplo: 14,5" /><Button title={error ? 'Reintentar envío' : 'Enviar reporte'} busyTitle="Enviando reporte…" icon="paper-plane-outline" busy={busy} disabled={minutes === null} onPress={() => void send()} /></>}
      <Button secondary title="Volver sin enviar" disabled={busy} onPress={() => router.back()} />
    </Card> : <><Notice error>No encontramos esta consulta en tu historial cargado.</Notice><Button title="Volver" onPress={() => router.back()} /></>}
  </Page></KeyboardAvoidingView>;
}
