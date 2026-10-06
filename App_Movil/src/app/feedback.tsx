import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform } from 'react-native';
import { Button, Card, Field, Label, Notice, Page, styles } from '@/components/arribo-ui';
import { useArribo } from '@/state/arribo-provider';
import { formatDate, parseMinutes } from '@/utils/arribo';

export default function FeedbackScreen() {
  const { queryId } = useLocalSearchParams<{ queryId: string }>(); const { history, feedback } = useArribo();
  const entry = history.find(item => item.query_id === queryId);
  const [input, setInput] = useState(''); const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null); const [confirmation, setConfirmation] = useState<string | null>(null);
  const minutes = parseMinutes(input);
  async function send() {
    if (!entry || minutes === null) return;
    setBusy(true); setError(null);
    try { const result = await feedback(entry.query_id, minutes); setConfirmation(result === 'queued' ? 'Tu reporte está guardado y se enviará cuando haya conexión.' : entry.simulation ? 'Reporte guardado en la demostración.' : 'Gracias. Tu reporte fue recibido.'); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo enviar.'); } finally { setBusy(false); }
  }
  return <KeyboardAvoidingView style={styles.page} behavior={Platform.OS === 'ios' ? 'padding' : undefined}><Page title="¿Cuánto esperaste?" subtitle="Reporta al terminar tu espera. Es opcional.">
    {error && <Notice error>{error}</Notice>}
    {confirmation ? <><Notice>{confirmation}</Notice><Button title="Volver" onPress={() => router.back()} /></> : entry ? <Card>
      <Label>Consulta: {formatDate(entry.requested_at ?? entry.timestamp)}</Label><Label muted>Estimación: {entry.waiting_time_min.toFixed(1)} minutos</Label>
      {entry.actual_min !== undefined ? <Notice>Ya reportaste {entry.actual_min} minutos para esta consulta.</Notice> : <><Field label="Minutos de espera real" value={input} onChangeText={setInput} keyboardType="decimal-pad" placeholder="Por ejemplo: 14,5" /><Label muted>Ingresa un valor entre 0 y 180 minutos. Los retrasos largos también se conservan.</Label><Button title="Enviar reporte" busy={busy} disabled={minutes === null} onPress={() => void send()} /></>}
      <Button secondary title="Volver sin enviar" disabled={busy} onPress={() => router.back()} />
    </Card> : <><Notice error>No encontramos esta consulta en tu historial cargado.</Notice><Button title="Volver" onPress={() => router.back()} /></>}
  </Page></KeyboardAvoidingView>;
}
