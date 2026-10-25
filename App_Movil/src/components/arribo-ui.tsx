import { Ionicons } from '@expo/vector-icons';
import { ActivityIndicator, Animated, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, useAnimatedValue, useColorScheme, useWindowDimensions, View, type TextInputInstance, type TextInputProps } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { forwardRef, useEffect, useState, type ComponentProps, type ReactNode } from 'react';
import { useReducedMotion } from '@/hooks/use-reduced-motion';
import { useArribo } from '@/state/arribo-provider';
import { PALETTES, toneColors, type Tone } from '@/theme/palette';

export const tokens = { spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 }, radius: { field: 12, card: 22, pill: 100 }, layout: { page: 640, auth: 640 } };
export function usePalette() {
  const dark = useColorScheme() === 'dark';
  return { dark, ...PALETTES[dark ? 'dark' : 'light'] };
}
export function Label({ children, muted = false, style, ...props }: ComponentProps<typeof Text> & { children: ReactNode; muted?: boolean }) {
  const p = usePalette(); return <Text {...props} style={[styles.text, { color: muted ? p.muted : p.text }, style]}>{children}</Text>;
}
export function Card({ children, style }: { children: ReactNode; style?: ComponentProps<typeof View>['style'] }) {
  const p = usePalette(); return <View style={[styles.card, { backgroundColor: p.card, borderColor: p.border }, style]}>{children}</View>;
}
export function Badge({ children, tone = 'blue' }: { children: ReactNode; tone?: Tone }) {
  const p = usePalette(); const colors = toneColors(p, tone);
  return <View style={[styles.badge, { backgroundColor: colors.background }]}><Label style={{ color: colors.foreground, fontSize: 12, lineHeight: 18, fontWeight: '600' }}>{children}</Label></View>;
}
export function IconBadge({ name, tone = 'blue', size = 22 }: { name: ComponentProps<typeof Ionicons>['name']; tone?: Tone; size?: number }) {
  const p = usePalette(); const colors = toneColors(p, tone);
  return <View accessible={false} style={[styles.iconTile, { backgroundColor: colors.background }]}><Ionicons accessible={false} name={name} size={size} color={colors.foreground} /></View>;
}
export function Button({ title, onPress, busy = false, disabled = false, secondary = false, busyTitle = 'Un momento…', icon }: { title: string; onPress: () => void; busy?: boolean; disabled?: boolean; secondary?: boolean; busyTitle?: string; icon?: ComponentProps<typeof Ionicons>['name'] }) {
  const p = usePalette(); const color = secondary ? p.blue : p.white;
  return <Pressable accessibilityRole="button" accessibilityLabel={busy ? busyTitle : title} accessibilityState={{ disabled: disabled || busy, busy }} disabled={disabled || busy} onPress={onPress} style={({ pressed }) => [styles.button, { backgroundColor: secondary ? p.card : p.primary, borderColor: secondary ? p.fieldBorder : p.primary, opacity: disabled ? 0.55 : pressed ? 0.8 : 1 }]}>
    {busy && <ActivityIndicator color={color} />}<Text style={[styles.buttonText, { color }]}>{busy ? busyTitle : title}</Text>{!busy && icon && <Ionicons name={icon} size={20} color={color} />}
  </Pressable>;
}
export function TextAction({ title, onPress, disabled = false }: { title: string; onPress: () => void; disabled?: boolean }) {
  const p = usePalette(); return <Pressable accessibilityRole="button" accessibilityState={{ disabled }} disabled={disabled} onPress={onPress} style={({ pressed }) => ({ minHeight: 44, justifyContent: 'center', paddingVertical: 10, opacity: disabled ? 0.5 : pressed ? 0.65 : 1 })}><Label style={{ color: p.blue, fontWeight: '700' }}>{title}</Label></Pressable>;
}
export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  const p = usePalette(); const reduce = useReducedMotion(); const opacity = useAnimatedValue(1);
  useEffect(() => {
    if (reduce) { opacity.setValue(1); return; }
    opacity.setValue(0); const animation = Animated.timing(opacity, { toValue: 1, duration: 180, useNativeDriver: true });
    animation.start(); return () => animation.stop();
  }, [children, error, opacity, reduce]);
  return <Animated.View accessibilityRole={error ? 'alert' : undefined} accessibilityLiveRegion="polite" style={[styles.notice, { opacity, backgroundColor: error ? p.errorSurface : p.soft, borderColor: error ? p.danger : p.border }]}><Ionicons name={error ? 'alert-circle-outline' : 'information-circle-outline'} size={21} color={error ? p.danger : p.blue} /><Label style={[styles.flex, { color: error ? p.danger : p.text, fontSize: 14, lineHeight: 22 }]}>{children}</Label></Animated.View>;
}
export const Field = forwardRef<TextInputInstance, TextInputProps & { label: string; error?: string; helper?: string; icon?: ComponentProps<typeof Ionicons>['name'] }>(function Field({ label, error, helper, icon, secureTextEntry, ...props }, ref) {
  const p = usePalette(); const [focused, setFocused] = useState(false); const [revealed, setRevealed] = useState(false);
  return <View style={styles.gap}><Label style={styles.fieldLabel}>{label}</Label><View style={[styles.inputFrame, { backgroundColor: p.card, borderColor: error ? p.danger : focused ? p.blue : p.fieldBorder, borderWidth: focused || error ? 2 : 1 }]}>
    {icon && <Ionicons accessible={false} name={icon} size={20} color={error ? p.danger : p.blue} />}
    <TextInput ref={ref} accessibilityLabel={label} accessibilityHint={error ?? helper} placeholderTextColor={p.muted} {...props} secureTextEntry={secureTextEntry && !revealed} onFocus={event => { setFocused(true); props.onFocus?.(event); }} onBlur={event => { setFocused(false); props.onBlur?.(event); }} style={[styles.input, { color: p.text }, props.style]} />
    {secureTextEntry && <Pressable accessibilityRole="button" accessibilityLabel={`${revealed ? 'Ocultar' : 'Mostrar'} ${label.toLowerCase()}`} accessibilityState={{ disabled: props.editable === false }} disabled={props.editable === false} onPress={() => setRevealed(!revealed)} hitSlop={4} style={styles.eye}><Ionicons name={revealed ? 'eye-off-outline' : 'eye-outline'} size={22} color={p.blue} /></Pressable>}
  </View>{(error || helper) && <Label accessibilityLiveRegion="polite" style={{ fontSize: 13, lineHeight: 20, color: error ? p.danger : p.muted }}>{error ? `⚠ ${error}` : helper}</Label>}</View>;
});

export function Empty({
  icon, title, description, tone = 'accent',
}: {
  icon: ComponentProps<typeof Ionicons>['name'];
  title: string;
  description: string;
  tone?: Tone;
}) {
  return (
    <Card style={styles.empty}>
      <View style={{ alignSelf: 'center' }}>
        <IconBadge name={icon} tone={tone} size={30} />
      </View>
      <Label style={styles.cardTitle}>{title}</Label>
      <Label muted style={styles.center}>{description}</Label>
    </Card>
  );
}
export function Loading({ title = 'Cargando información…' }: { title?: string }) {
  const p = usePalette(); return <Card><View accessibilityLiveRegion="polite" style={styles.row}><ActivityIndicator color={p.blue} /><Label>{title}</Label></View></Card>;
}
export function Brand({ tone = 'blue' }: { tone?: Tone }) {
  const p = usePalette();
  const colors = toneColors(p, tone);
  return <View style={styles.row}><View style={[styles.logo, { backgroundColor: colors.background }]}><Ionicons accessible={false} name="bus-outline" size={18} color={colors.foreground} /></View><Text style={[styles.brand, { color: p.blue, flexShrink: 1 }]}>ARRIBO</Text></View>;
}
export function Page({ title, subtitle, children, brandTone = 'blue' }: { title: string; subtitle: string; children: ReactNode; brandTone?: Tone }) {
  const p = usePalette(); const { mode, online, error, accountNotice } = useArribo(); const { width } = useWindowDimensions();
  return <SafeAreaView edges={['top', 'left', 'right']} style={[styles.page, { backgroundColor: p.background }]}><ScrollView keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" contentContainerStyle={[styles.content, { paddingHorizontal: width < 380 ? 16 : 24 }]}><View style={styles.header}><Brand tone={brandTone} /><Label accessibilityRole="header" style={styles.title}>{title}</Label><Label muted>{subtitle}</Label></View>{mode === 'demo' && <Notice>Demostración · Las estimaciones y avisos son ejemplos.</Notice>}{!online && <Notice>Sin conexión · Los datos guardados pueden estar desactualizados.</Notice>}{accountNotice && <Notice>{accountNotice}</Notice>}{error && <Notice error>{error}</Notice>}{children}</ScrollView></SafeAreaView>;
}
export function AuthPage({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  const p = usePalette(); const { width } = useWindowDimensions(); const { online, error } = useArribo();
  return <SafeAreaView style={[styles.page, { backgroundColor: p.background }]}><KeyboardAvoidingView style={styles.page} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}><ScrollView keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" contentContainerStyle={[styles.authContent, { paddingHorizontal: width < 380 ? 16 : 24 }]}>
    <View style={styles.authLayout}><View style={styles.header}><Brand /><Label accessibilityRole="header" style={styles.title}>{title}</Label><Label muted>{subtitle}</Label></View>
      {!online && <Notice>Sin conexión. Necesitas internet para acceder a tu cuenta.</Notice>}{error && <Notice error>{error}</Notice>}
      <Card style={styles.authForm}>{children}</Card>
    </View>
  </ScrollView></KeyboardAvoidingView></SafeAreaView>;
}
export const styles = StyleSheet.create({
  page: { flex: 1 }, content: { paddingTop: 24, gap: 20, maxWidth: tokens.layout.page, width: '100%', alignSelf: 'center', paddingBottom: 40 },
  text: { fontSize: 16, lineHeight: 24 }, title: { fontSize: 30, lineHeight: 38, fontWeight: '700' }, brand: { fontSize: 12, fontWeight: '800', letterSpacing: 3 }, logo: { padding: 6, borderRadius: 10 }, header: { gap: 8 },
  card: { padding: 20, borderRadius: tokens.radius.card, borderWidth: 1, gap: 12 }, cardTitle: { fontSize: 18, lineHeight: 26, fontWeight: '700' },
  button: { minHeight: 52, padding: 14, borderRadius: 16, borderWidth: 1, flexDirection: 'row', gap: 10, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center' }, buttonText: { fontSize: 16, lineHeight: 24, fontWeight: '700', textAlign: 'center', flexShrink: 1 },
  notice: { borderWidth: 1, borderRadius: 14, padding: 14, flexDirection: 'row', alignItems: 'flex-start', gap: 10 }, fieldLabel: { fontSize: 14, lineHeight: 22, fontWeight: '600' }, inputFrame: { flexDirection: 'row', alignItems: 'center', borderRadius: tokens.radius.field, paddingLeft: 14, minHeight: 52 }, input: { flex: 1, minWidth: 0, padding: 12, fontSize: 16, minHeight: 50 }, eye: { minHeight: 48, minWidth: 48, justifyContent: 'center', alignItems: 'center' },
  badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: tokens.radius.pill, alignSelf: 'flex-start' }, row: { flexDirection: 'row', alignItems: 'center', gap: 12 }, headingRow: { flexDirection: 'row', alignItems: 'center', gap: 12, flexGrow: 1, flexBasis: 180, maxWidth: '100%' }, between: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 14 }, flex: { flex: 1, minWidth: 0 }, gap: { gap: 8 }, center: { textAlign: 'center' }, empty: { alignItems: 'center', paddingVertical: 32 }, iconTile: { padding: 10, borderRadius: 14, alignSelf: 'flex-start' }, value: { fontSize: 64, lineHeight: 74, fontWeight: '700', letterSpacing: -2 }, metricRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'baseline', gap: 12 },
  authContent: { flexGrow: 1, paddingTop: 24, paddingBottom: 32 }, authLayout: { maxWidth: tokens.layout.auth, width: '100%', alignSelf: 'center', gap: 20 }, authForm: { gap: 16 }, divider: { borderTopWidth: 1, paddingTop: 12, gap: 8 },
});
