import { Ionicons } from '@expo/vector-icons';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, useColorScheme, View, type TextInputProps } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { ComponentProps, ReactNode } from 'react';
import { useArribo } from '@/state/arribo-provider';

export function usePalette() {
  const dark = useColorScheme() === 'dark';
  return { dark, background: dark ? '#141416' : '#f5f5f7', card: dark ? '#242427' : '#ffffff', text: dark ? '#f5f5f7' : '#1d1d1f', muted: dark ? '#b3b3bc' : '#62626c', border: dark ? '#3a3a40' : '#e4e4ea', blue: dark ? '#2997ff' : '#0071e3', green: dark ? '#30d158' : '#167544', danger: dark ? '#ff786d' : '#b42318' };
}
export function Label({ children, muted = false, style }: { children: ReactNode; muted?: boolean; style?: ComponentProps<typeof Text>['style'] }) {
  const p = usePalette(); return <Text style={[styles.text, { color: muted ? p.muted : p.text }, style]}>{children}</Text>;
}
export function Card({ children, style }: { children: ReactNode; style?: ComponentProps<typeof View>['style'] }) {
  const p = usePalette(); return <View style={[styles.card, { backgroundColor: p.card, borderColor: p.border }, style]}>{children}</View>;
}
export function Button({ title, onPress, busy = false, disabled = false, secondary = false }: { title: string; onPress: () => void; busy?: boolean; disabled?: boolean; secondary?: boolean }) {
  const p = usePalette(); return <Pressable accessibilityRole="button" accessibilityState={{ disabled: disabled || busy, busy }} disabled={disabled || busy} onPress={onPress} style={({ pressed }) => [styles.button, { backgroundColor: secondary ? p.card : p.blue, borderColor: p.border, opacity: disabled || busy ? 0.5 : pressed ? 0.75 : 1 }]}>{busy ? <ActivityIndicator color={secondary ? p.blue : '#fff'} /> : <Text style={[styles.buttonText, { color: secondary ? p.blue : '#fff' }]}>{title}</Text>}</Pressable>;
}
export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  const p = usePalette(); return <View accessibilityRole={error ? 'alert' : undefined} style={[styles.notice, { backgroundColor: p.card, borderColor: error ? p.danger : p.border }]}><Ionicons name={error ? 'alert-circle-outline' : 'information-circle-outline'} size={21} color={error ? p.danger : p.blue} /><Label style={styles.flex}>{children}</Label></View>;
}
export function Field({ label, ...props }: TextInputProps & { label: string }) {
  const p = usePalette(); return <View style={styles.gap}><Label muted>{label}</Label><TextInput accessibilityLabel={label} placeholderTextColor={p.muted} {...props} style={[styles.input, { color: p.text, borderColor: p.border, backgroundColor: p.card }, props.style]} /></View>;
}
export function Empty({ icon, title, description }: { icon: ComponentProps<typeof Ionicons>['name']; title: string; description: string }) {
  const p = usePalette(); return <Card style={styles.empty}><Ionicons name={icon} size={36} color={p.blue} /><Label style={styles.cardTitle}>{title}</Label><Label muted style={styles.center}>{description}</Label></Card>;
}
export function Page({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  const p = usePalette(); const { mode, online, error } = useArribo();
  return <SafeAreaView edges={['top', 'left', 'right']} style={[styles.page, { backgroundColor: p.background }]}><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.content}><View style={styles.gap}><Text style={[styles.brand, { color: p.blue }]}>ARRIBO</Text><Label style={styles.title}>{title}</Label><Label muted>{subtitle}</Label></View>{mode === 'demo' && <Notice>Datos de demostración · Las estimaciones y avisos son ejemplos.</Notice>}{!online && <Notice>Sin conexión · Los datos guardados pueden estar desactualizados.</Notice>}{error && <Notice error>{error}</Notice>}{children}</ScrollView></SafeAreaView>;
}
export const styles = StyleSheet.create({
  page: { flex: 1 }, content: { padding: 24, gap: 20, maxWidth: 640, width: '100%', alignSelf: 'center', paddingBottom: 40 },
  text: { fontSize: 16, lineHeight: 24 }, title: { fontSize: 30, lineHeight: 38, fontWeight: '700' }, brand: { fontSize: 12, fontWeight: '800', letterSpacing: 3 },
  card: { padding: 20, borderRadius: 22, borderWidth: 1, gap: 12 }, cardTitle: { fontSize: 18, fontWeight: '700' },
  button: { minHeight: 52, padding: 14, borderRadius: 16, borderWidth: 1, alignItems: 'center', justifyContent: 'center' }, buttonText: { fontSize: 16, fontWeight: '700' },
  notice: { borderWidth: 1, borderRadius: 14, padding: 14, flexDirection: 'row', alignItems: 'flex-start', gap: 10 }, input: { borderWidth: 1, borderRadius: 12, minHeight: 52, padding: 14, fontSize: 16 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12 }, between: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 14 }, flex: { flex: 1 }, gap: { gap: 8 }, center: { textAlign: 'center' }, empty: { alignItems: 'center', paddingVertical: 32 }, value: { fontSize: 64, lineHeight: 74, fontWeight: '700', letterSpacing: -2 },
});
