import { Ionicons } from '@expo/vector-icons';
import { Tabs } from 'expo-router';
import { usePalette } from '@/components/arribo-ui';
export default function TabsLayout() {
  const p = usePalette();
  return <Tabs screenOptions={{ headerShown: false, tabBarActiveTintColor: p.blue, tabBarInactiveTintColor: p.muted, tabBarLabelStyle: { fontSize: 12, fontWeight: '600' }, tabBarHideOnKeyboard: true, tabBarStyle: { backgroundColor: p.card, borderTopColor: p.border } }}>
    <Tabs.Screen name="index" options={{ title: 'Predicción', tabBarIcon: ({ color, size, focused }) => <Ionicons name={focused ? 'bus' : 'bus-outline'} color={color} size={size} /> }} />
    <Tabs.Screen name="history" options={{ title: 'Historial', tabBarIcon: ({ color, size, focused }) => <Ionicons name={focused ? 'time' : 'time-outline'} color={color} size={size} /> }} />
    <Tabs.Screen name="alerts" options={{ title: 'Alertas', tabBarIcon: ({ color, size, focused }) => <Ionicons name={focused ? 'notifications' : 'notifications-outline'} color={color} size={size} /> }} />
    <Tabs.Screen name="settings" options={{ title: 'Configuración', tabBarIcon: ({ color, size, focused }) => <Ionicons name={focused ? 'settings' : 'settings-outline'} color={color} size={size} /> }} />
  </Tabs>;
}
