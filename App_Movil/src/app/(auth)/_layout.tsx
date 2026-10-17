import { Stack } from 'expo-router';
import { useReducedMotion } from '@/hooks/use-reduced-motion';

export default function AuthLayout() {
  const reduced = useReducedMotion();
  return <Stack screenOptions={{ headerShown: false, animation: reduced ? 'none' : 'fade' }}>
    <Stack.Screen name="index" />
    <Stack.Screen name="register" />
    <Stack.Screen name="recover" />
  </Stack>;
}
