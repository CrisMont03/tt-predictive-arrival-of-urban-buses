import { router, useLocalSearchParams } from 'expo-router';
import { useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, View } from 'react-native';
import { Badge, Button, Card, Field, IconBadge, Label, Notice, Page, styles } from '@/components/arribo-ui';
import { useArribo } from '@/state/arribo-provider';
import { stationName } from '@/data/stations';
import { formatDate, parseMinutes } from '@/utils/arribo';

export default function FeedbackScreen() {
  const { queryId } = useLocalSearchParams<{ queryId: string }>(); const { history, feedback } = useArribo();
  const entry = history.find(item => item.query_id === queryId);
  const [input, setInput] = useState(''); const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null); const [confirmation, setConfirmation] = useState<string | null>(null);
  const minutes = parseMinutes(input);
  const locked = useRef(false); const [touched, setTouched] = useState(false);
  async function send() {
    if (locked.current || !entry || minutes === null) return;
    locked.current = true;
    setBusy(true); setError(null);
    try { const result = await feedback(entry.query_id, minutes); setConfirmation(result === 'queued' ? 'Tu reporte está guardado y se enviará cuando haya conexión.' : entry.simulation ? 'Reporte guardado en la demostración.' : 'Gracias. Tu reporte fue recibido.'); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo enviar.'); } finally { locked.current = false; setBusy(false); }
  }
  return <KeyboardAvoidingView style={styles.page} behavior={Platform.OS === 'ios' ? 'padding' : undefined}><Page title="¿Cuánto esperaste?" subtitle="Reporta al terminar tu espera. Es opcional.">
    {error && <Notice error>{error}</Notice>}
    {confirmation ? <><Notice>{confirmation}</Notice><Button title="Volver" onPress={() => router.back()} /></> : entry ? <Card>
      <View style={styles.row}><IconBadge name="chatbubble-ellipses-outline" /><Badge tone={entry.simulation ? 'muted' : 'accent'}>{entry.simulation ? 'Consulta de demostración' : 'Tu consulta'}</Badge></View><Label>Estación: {stationName(entry.station_id)}</Label><Label>Consulta: {formatDate(entry.requested_at ?? entry.timestamp)}</Label><Label muted>Estimación: {entry.waiting_time_min.toFixed(1)} minutos</Label>
      {entry.actual_min !== undefined ? <Notice>Ya reportaste {entry.actual_min} minutos para esta consulta.{entry.feedback_pending ? ' Está pendiente de envío.' : ''}</Notice> : <><Field label="Minutos de espera real" value={input} onChangeText={setInput} onBlur={() => setTouched(true)} error={touched && minutes === null ? 'Ingresa un número entre 0 y 180, con hasta dos decimales.' : undefined} helper="Los retrasos largos también se conservan." editable={!busy} keyboardType="decimal-pad" placeholder="Por ejemplo: 14,5" /><Button title={error ? 'Reintentar envío' : 'Enviar reporte'} busyTitle="Enviando reporte…" icon="paper-plane-outline" busy={busy} disabled={minutes === null} onPress={() => void send()} /></>}
      <Button secondary title="Volver sin enviar" disabled={busy} onPress={() => router.back()} />
    </Card> : <><Notice error>No encontramos esta consulta en tu historial cargado.</Notice><Button title="Volver" onPress={() => router.back()} /></>}
  </Page></KeyboardAvoidingView>;
}
