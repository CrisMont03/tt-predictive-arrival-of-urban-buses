import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { ActivityIndicator, View } from 'react-native';
import { ArriboProvider, useArribo } from '@/state/arribo-provider';
import { usePalette } from '@/components/arribo-ui';

void SplashScreen.preventAutoHideAsync();
function Navigation() {
  const { booting, session } = useArribo(); const p = usePalette();
  useEffect(() => { if (!booting) void SplashScreen.hideAsync(); }, [booting]);
  if (booting) return <View style={{ flex: 1, justifyContent: 'center', backgroundColor: p.background }}><ActivityIndicator color={p.blue} /></View>;
  return <><StatusBar style={p.dark ? 'light' : 'dark'} /><Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: p.background } }}><Stack.Protected guard={!session}><Stack.Screen name="(auth)" /></Stack.Protected><Stack.Protected guard={!!session}><Stack.Screen name="(tabs)" /><Stack.Screen name="feedback" options={{ presentation: 'modal' }} /></Stack.Protected></Stack></>;
}
export default function RootLayout() { return <ArriboProvider><Navigation /></ArriboProvider>; }
