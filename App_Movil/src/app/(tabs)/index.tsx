import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { View } from 'react-native';
import { Button, Card, Label, Notice, Page, styles, usePalette } from '@/components/arribo-ui';
import { STATION } from '@/config/arribo';
import { useArribo } from '@/state/arribo-provider';
import type { Prediction } from '@/types/arribo';
import { distanceMeters, formatDate } from '@/utils/arribo';

export default function PredictionScreen() {
  const { predict, dataReady, preferences } = useArribo(); const p = usePalette();
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [far, setFar] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 15000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    let alive = true;
    if (preferences.gps_enabled) Location.getForegroundPermissionsAsync().then(permission => permission.granted ? Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }) : null).then(position => {
      if (alive && position) setFar(distanceMeters(position.coords.latitude, position.coords.longitude, STATION.latitude, STATION.longitude) > 200);
    }).catch(() => { if (alive) setError('No se pudo obtener tu ubicación. Puedes consultar la espera igualmente.'); });
    return () => { alive = false; };
  }, [preferences.gps_enabled]);
  async function consult() {
    setBusy(true); setError(null);
    try { setPrediction(await predict()); } catch (failure) { setError(failure instanceof Error ? failure.message : 'No se pudo consultar.'); } finally { setBusy(false); }
  }
  return <Page title="¿Cuánto falta?" subtitle="Una estimación para organizar tu espera.">
    <Card><View style={styles.row}><Ionicons name="location-outline" color={p.blue} size={26} /><View style={styles.flex}><Label style={styles.cardTitle}>{STATION.name}</Label><Label muted>{STATION.city}</Label></View></View><Label muted>Estación disponible</Label></Card>
    {far && preferences.gps_enabled && <Notice>Parece que estás lejos de la parada. La estimación aplica para Río Consulado – Misterios.</Notice>}{error && <Notice error>{error}</Notice>}
    {prediction ? <Card>
      {prediction.simulation && <Notice>Datos de demostración · Este resultado es un ejemplo.</Notice>}
      <Label muted>Tiempo estimado de espera</Label><View style={styles.row}><Label style={[styles.value, { color: p.blue }]}>{Math.round(prediction.waiting_time_min)}</Label><Label muted>minutos</Label></View>
      <Label>Entre {Math.round(prediction.confidence_interval[0])} y {Math.round(prediction.confidence_interval[1])} minutos</Label><Label muted>Estimación generada: {formatDate(prediction.timestamp)}</Label>
      {Date.parse(prediction.expires_at) <= now && <Notice>Esta estimación ha vencido. Consulta de nuevo para actualizarla.</Notice>}
      <View style={styles.row}><Ionicons name={prediction.context.precipitation_mm ? 'rainy-outline' : 'partly-sunny-outline'} color={p.blue} size={22} /><Label>{prediction.context.temp_c === null ? 'Clima no disponible' : `${prediction.context.temp_c} °C`}</Label></View>
      {prediction.context.traffic_density !== null && prediction.context.traffic_density < 0.5 && <Notice>Tráfico lento en la zona.</Notice>}{prediction.context.env_alert_active === 1 && <Notice>Hay una alerta ambiental activa. Consulta Alertas.</Notice>}
      <Button secondary title="Reportar cuánto esperé" onPress={() => router.push({ pathname: '/feedback', params: { queryId: prediction.query_id } })} />
    </Card> : <Card><Label style={styles.cardTitle}>Consulta cuando estés por viajar</Label><Label muted>Obtendrás una estimación y un rango de espera. No es un seguimiento GPS del autobús.</Label></Card>}
    <Button title={prediction ? 'Actualizar estimación' : 'Obtener predicción'} disabled={!dataReady} busy={busy} onPress={() => void consult()} />
  </Page>;
}
