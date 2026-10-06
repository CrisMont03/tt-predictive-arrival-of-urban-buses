import { Ionicons } from '@expo/vector-icons';
import { Tabs } from 'expo-router';
import { usePalette } from '@/components/arribo-ui';
export default function TabsLayout() {
  const p = usePalette();
  return <Tabs screenOptions={{ headerShown: false, tabBarActiveTintColor: p.blue, tabBarInactiveTintColor: p.muted, tabBarStyle: { backgroundColor: p.card, borderTopColor: p.border } }}>
    <Tabs.Screen name="index" options={{ title: 'Predicción', tabBarIcon: ({ color, size }) => <Ionicons name="bus-outline" color={color} size={size} /> }} />
    <Tabs.Screen name="history" options={{ title: 'Historial', tabBarIcon: ({ color, size }) => <Ionicons name="time-outline" color={color} size={size} /> }} />
    <Tabs.Screen name="alerts" options={{ title: 'Alertas', tabBarIcon: ({ color, size }) => <Ionicons name="notifications-outline" color={color} size={size} /> }} />
    <Tabs.Screen name="settings" options={{ title: 'Configuración', tabBarIcon: ({ color, size }) => <Ionicons name="options-outline" color={color} size={size} /> }} />
  </Tabs>;
}
