import PredictionScreen from '@/screens/prediction-screen';
import { useArribo } from '@/state/arribo-provider';

export default function PredictionRoute() {
  const { selectedStation, session } = useArribo();
  // A fresh screen clears old estimates, location notices and errors. Its
  // pending requests cannot set state after unmounting.
  return <PredictionScreen key={`${session?.uid}:${selectedStation.id}`} />;
}
