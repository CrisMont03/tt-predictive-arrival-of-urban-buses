import { Ionicons } from '@expo/vector-icons';
import { Tabs } from 'expo-router';
import { usePalette } from '@/components/arribo-ui';
export default function TabsLayout() {
  const p = usePalette();
  return <Tabs screenOptions={{ headerShown: false, tabBarActiveTintColor: p.blue, tabBarInactiveTintColor: p.muted, tabBarLabelStyle: { fontSize: 12, fontWeight: '600' }, tabBarHideOnKeyboard: true, tabBarStyle: { backgroundColor: p.card, borderTopColor: p.border } }}>
    <Tabs.Screen name="index" options={{ title: 'Predicción', tabBarIcon: ({ size, focused }) => <Ionicons name={focused ? 'bus' : 'bus-outline'} color={focused ? p.blue : p.muted} size={size} /> }} />
    <Tabs.Screen name="history" options={{ title: 'Historial', tabBarIcon: ({ size, focused }) => <Ionicons name={focused ? 'time' : 'time-outline'} color={focused ? p.blue : p.muted} size={size} /> }} />
    <Tabs.Screen name="alerts" options={{ title: 'Alertas', tabBarIcon: ({ size, focused }) => <Ionicons name={focused ? 'notifications' : 'notifications-outline'} color={focused ? p.blue : p.muted} size={size} /> }} />
    <Tabs.Screen name="settings" options={{ title: 'Configuración', tabBarIcon: ({ size, focused }) => <Ionicons name={focused ? 'settings' : 'settings-outline'} color={focused ? p.blue : p.muted} size={size} /> }} />
  </Tabs>;
}
