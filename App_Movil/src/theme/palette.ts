// Neutral surfaces from the original ARRIBO layout; colored accents are an
// adaptation of Plata's public screenshots, not a copy of its brand assets.
export const PALETTES = {
  light: {
    background: '#F5F5F7', card: '#FFFFFF', text: '#202024', muted: '#61616C',
    border: '#E4E4EA', fieldBorder: '#858590', blue: '#0068D6', primary: '#0068D6',
    soft: '#EAF2FF', accent: '#AD450C', accentSoft: '#FFF0E6',
    violet: '#653BC2', violetSoft: '#F0EAFC', green: '#176449', greenSoft: '#EAF7F0',
    warning: '#80530B', warningSoft: '#FFF5DE', danger: '#B12D35', errorSurface: '#FFF2F2',
    neutralSoft: '#EFEFF3', white: '#FFFFFF',
  },
  dark: {
    background: '#141416', card: '#242427', text: '#F5F5F7', muted: '#B3B3BC',
    border: '#3A3A40', fieldBorder: '#858590', blue: '#80B8FF', primary: '#0A64C9',
    soft: '#20344F', accent: '#FFB17E', accentSoft: '#3E2B20',
    violet: '#C8B1FF', violetSoft: '#33274B', green: '#81D6B4', greenSoft: '#203B30',
    warning: '#F1CD85', warningSoft: '#3E321C', danger: '#FFABA5', errorSurface: '#3E262B',
    neutralSoft: '#303035', white: '#FFFFFF',
  },
} as const;

export type Tone = 'blue' | 'accent' | 'violet' | 'green' | 'warning' | 'danger' | 'muted';
export function toneColors(palette: typeof PALETTES.light | typeof PALETTES.dark, tone: Tone) {
  const backgrounds = { blue: palette.soft, accent: palette.accentSoft, violet: palette.violetSoft, green: palette.greenSoft, warning: palette.warningSoft, danger: palette.errorSurface, muted: palette.neutralSoft };
  return { foreground: palette[tone], background: backgrounds[tone] };
}
