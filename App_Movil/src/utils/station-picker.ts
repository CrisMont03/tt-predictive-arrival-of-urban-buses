export type StationPickerState = { visible: boolean; search: string };
export type StationPickerAction = { type: 'open' } | { type: 'close' } | { type: 'search'; value: string };
export function stationPickerReducer(state: StationPickerState, action: StationPickerAction): StationPickerState {
  if (action.type === 'open') return { visible: true, search: '' };
  if (action.type === 'close') return { visible: false, search: '' };
  return state.visible ? { ...state, search: action.value } : state;
}
