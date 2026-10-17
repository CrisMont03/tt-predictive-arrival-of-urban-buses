import { Ionicons } from '@expo/vector-icons';
import * as Location from 'expo-location';
import { router } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { View } from 'react-native';
import { Badge, Button, Card, IconBadge, Label, Loading, Notice, Page, styles, usePalette } from '@/components/arribo-ui';
import { StationSelector } from '@/components/station-selector';
import { stationAvailable } from '@/data/stations';
import { StaleStationPrediction } from '@/utils/station-selection';
import { useArribo } from '@/state/arribo-provider';
import type { Prediction } from '@/types/arribo';
import { distanceMeters, formatDate } from '@/utils/arribo';

export default function PredictionScreen() {
  const { predict, dataReady, preferences, selectedStation, selectStation, mode } = useArribo(); const p = usePalette();
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [far, setFar] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const locked = useRef(false); const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 15000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    let alive = true;
    if (preferences.gps_enabled) Location.getForegroundPermissionsAsync().then(permission => permission.granted ? Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }) : null).then(position => {
      if (alive && position) setFar(distanceMeters(position.coords.latitude, position.coords.longitude, selectedStation.latitude, selectedStation.longitude) > 200);
    }).catch(() => { if (alive) setError('No se pudo obtener tu ubicación. Puedes consultar la espera igualmente.'); });
    return () => { alive = false; };
  }, [preferences.gps_enabled, selectedStation.latitude, selectedStation.longitude]);
  async function consult() {
    if (locked.current) return;
    locked.current = true;
    setBusy(true); setError(null);
    try { const result = await predict(); if (alive.current) setPrediction(result); } catch (failure) { if (alive.current && !(failure instanceof StaleStationPrediction)) setError(failure instanceof Error ? failure.message : 'No se pudo consultar.'); } finally { locked.current = false; if (alive.current) setBusy(false); }
  }
  return <Page title="¿Cuánto falta?" subtitle="Una estimación para organizar tu espera.">
    <StationSelector station={selectedStation} mode={mode} onSelect={selectStation} disabled={!dataReady} />
    {far && preferences.gps_enabled && <Notice>Parece que estás lejos de la parada. La estimación aplica para {selectedStation.name}.</Notice>}{error && <Notice error>{error}</Notice>}
    {!dataReady && <Loading title="Preparando tu sesión…" />}{busy && <Loading title="Obteniendo una estimación. Puede tardar unos segundos…" />}
    {prediction ? <Card>
      {prediction.simulation && <Notice>Datos de demostración · Este resultado es un ejemplo.</Notice>}
      <View style={styles.between}><Label muted>Tiempo estimado de espera</Label><Badge tone={Date.parse(prediction.expires_at) <= now ? 'warning' : 'blue'}>{Date.parse(prediction.expires_at) <= now ? 'Vencida' : 'Estimación vigente'}</Badge></View><View style={styles.metricRow}><Label style={[styles.value, { color: p.blue }]}>{Math.round(prediction.waiting_time_min)}</Label><Label muted>minutos</Label></View>
      <Label>Entre {Math.round(prediction.confidence_interval[0])} y {Math.round(prediction.confidence_interval[1])} minutos</Label><Label muted style={{ fontSize: 13 }}>Generada: {formatDate(prediction.timestamp)}</Label><Label muted style={{ fontSize: 13 }}>Fuente: {prediction.source === 'mock' ? 'Demostración' : prediction.source === 'cache' ? 'Caché' : 'Modelo predictivo'} · {prediction.model_version}</Label>
      {Date.parse(prediction.expires_at) <= now && <Notice>Esta estimación ha vencido. Consulta de nuevo para actualizarla.</Notice>}
      <View style={styles.row}><Ionicons name={prediction.context.precipitation_mm ? 'rainy-outline' : 'partly-sunny-outline'} color={p.blue} size={22} /><Label>{prediction.context.temp_c === null ? 'Clima no disponible' : `${prediction.context.temp_c} °C`}</Label></View>
      {prediction.context.traffic_density !== null && prediction.context.traffic_density < 0.5 && <Notice>Tráfico lento en la zona.</Notice>}{prediction.context.env_alert_active === 1 && <Notice>Hay una alerta ambiental activa. Consulta Alertas.</Notice>}
      <Button secondary title="Reportar cuánto esperé" icon="chatbubble-outline" onPress={() => router.push({ pathname: '/feedback', params: { queryId: prediction.query_id } })} />
    </Card> : <Card><View style={styles.row}><IconBadge name="bus-outline" /><Label style={[styles.cardTitle, styles.flex]}>Consulta cuando estés por viajar</Label></View><Label muted>Obtendrás una estimación y un rango de espera. No es un seguimiento GPS del autobús.</Label></Card>}
    <Button title={error ? 'Reintentar consulta' : prediction ? 'Actualizar estimación' : 'Obtener predicción'} busyTitle="Consultando…" icon="arrow-forward" disabled={!dataReady || !stationAvailable(selectedStation, mode)} busy={busy} onPress={() => void consult()} />
  </Page>;
}
