import { Ionicons } from '@expo/vector-icons';
import { useMemo, useReducer } from 'react';
import { FlatList, Keyboard, KeyboardAvoidingView, Modal, Platform, Pressable, StyleSheet, useWindowDimensions, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Badge, Card, Field, IconBadge, Label, styles, usePalette } from '@/components/arribo-ui';
import { filterStations, stationAvailable } from '@/data/stations';
import { useReducedMotion } from '@/hooks/use-reduced-motion';
import type { Station } from '@/types/arribo';
import { stationPickerReducer } from '@/utils/station-picker';

export function StationSelector({ station, mode, onSelect, disabled = false }: {
  station: Station; mode: 'demo' | 'live'; onSelect: (id: string) => void; disabled?: boolean;
}) {
  const p = usePalette(); const insets = useSafeAreaInsets(); const { height } = useWindowDimensions(); const reduced = useReducedMotion();
  const [{ visible, search }, dispatch] = useReducer(stationPickerReducer, { visible: false, search: '' });
  const stations = useMemo(() => filterStations(search), [search]);
  function close() { Keyboard.dismiss(); dispatch({ type: 'close' }); }
  function select(item: Station) {
    if (!stationAvailable(item, mode)) return;
    onSelect(item.id); close();
  }
  return <>
    <Pressable accessibilityRole="button" accessibilityLabel={`Elegir estación. Seleccionada: ${station.name}. ${station.zone}${station.demonstration ? '. Demostración' : ''}`} accessibilityHint="Abre la búsqueda de estaciones" accessibilityState={{ disabled, expanded: visible }} disabled={disabled} onPress={() => dispatch({ type: 'open' })} style={({ pressed }) => ({ opacity: pressed || disabled ? 0.65 : 1 })}>
      <Card><View style={styles.row}><IconBadge name="location-outline" /><View style={styles.flex}><Label style={styles.cardTitle}>{station.name}</Label><Label muted>{station.zone}</Label></View><Ionicons accessible={false} name="chevron-down" size={22} color={p.blue} /></View>{station.demonstration && <Badge>Estación de demostración</Badge>}</Card>
    </Pressable>
    <Modal visible={visible} transparent animationType={reduced ? 'none' : 'slide'} onRequestClose={close} statusBarTranslucent supportedOrientations={['portrait', 'landscape']}>
      <View style={sheet.overlay}>
        <Pressable accessible={false} onPress={close} style={[StyleSheet.absoluteFill, { backgroundColor: '#00000066' }]} />
        <KeyboardAvoidingView pointerEvents="box-none" style={[sheet.avoidKeyboard, { paddingTop: insets.top + 12 }]} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
          <View accessibilityViewIsModal onAccessibilityEscape={close} style={[sheet.panel, { backgroundColor: p.card, borderColor: p.border, height: height * 0.76, paddingBottom: Math.max(insets.bottom, 16), paddingLeft: 20 + insets.left, paddingRight: 20 + insets.right }]}>
            <View style={[sheet.handle, { backgroundColor: p.border }]} />
            <View style={styles.row}><Label accessibilityRole="header" style={[styles.cardTitle, styles.flex, { fontSize: 22 }]}>Elegir estación</Label><Pressable accessibilityRole="button" accessibilityLabel="Cerrar selector de estación" onPress={close} style={sheet.close}><Ionicons name="close" size={26} color={p.blue} /></Pressable></View>
            <Field label="Buscar por nombre o zona" icon="search-outline" placeholder="Por ejemplo: Río Consulado" value={search} onChangeText={value => dispatch({ type: 'search', value })} autoCorrect={false} autoCapitalize="none" returnKeyType="search" onSubmitEditing={Keyboard.dismiss} />
            <FlatList style={sheet.list} data={stations} keyExtractor={item => item.id} extraData={`${station.id}:${mode}`} keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" contentContainerStyle={sheet.listContent} ItemSeparatorComponent={() => <View style={{ height: 8 }} />} ListEmptyComponent={<View style={sheet.empty}><IconBadge name="search-outline" /><Label style={styles.cardTitle}>No encontramos estaciones</Label><Label muted>Prueba con otro nombre o zona.</Label></View>} renderItem={({ item }) => {
              const enabled = stationAvailable(item, mode); const selected = item.id === station.id;
              return <Pressable accessibilityRole="radio" accessibilityLabel={`${item.name}. ${item.zone}${item.demonstration ? '. Demostración' : ''}${enabled ? '' : '. No disponible para consultas reales'}`} accessibilityState={{ checked: selected, disabled: !enabled }} disabled={!enabled} onPress={() => select(item)} style={({ pressed }) => [sheet.item, { borderColor: selected ? p.blue : p.border, backgroundColor: selected ? p.soft : p.card, opacity: pressed ? 0.7 : 1 }]}>
                <View style={styles.row}><IconBadge name="location-outline" /><View style={styles.flex}><Label style={{ fontWeight: '600' }}>{item.name}</Label><Label muted style={{ fontSize: 13 }}>{item.zone}</Label></View>{selected ? <Ionicons accessible={false} name="checkmark-circle" size={24} color={p.blue} /> : !enabled ? <Ionicons accessible={false} name="lock-closed-outline" size={20} color={p.muted} /> : null}</View>
                {item.demonstration && <Badge tone={enabled ? 'blue' : 'muted'}>{enabled ? 'Demostración · datos simulados' : 'Demostración · solo en modo demo'}</Badge>}
              </Pressable>;
            }} />
          </View>
        </KeyboardAvoidingView>
      </View>
    </Modal>
  </>;
}
const sheet = StyleSheet.create({
  overlay: { flex: 1 }, avoidKeyboard: { flex: 1, justifyContent: 'flex-end' },
  // El panel debe encogerse cuando el teclado reduce el espacio disponible.
  panel: { flexShrink: 1, minHeight: 0, maxHeight: '100%', maxWidth: 640, width: '100%', alignSelf: 'center', borderWidth: 1, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingHorizontal: 20, paddingTop: 10, gap: 12 },
  handle: { width: 40, height: 4, borderRadius: 4, alignSelf: 'center' }, close: { minWidth: 48, minHeight: 48, justifyContent: 'center', alignItems: 'center' },
  list: { flex: 1, minHeight: 0 }, listContent: { paddingVertical: 8 }, item: { padding: 14, borderRadius: 16, borderWidth: 1, gap: 8 }, empty: { paddingVertical: 24, gap: 12, alignItems: 'center' },
});
