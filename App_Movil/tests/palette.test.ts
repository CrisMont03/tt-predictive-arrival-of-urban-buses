/// <reference types="node" />
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { PALETTES, toneColors, type Tone } from '../src/theme/palette.ts';

function luminance(hex: string) {
  const channels = [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}
function contrast(first: string, second: string) {
  const a = luminance(first); const b = luminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

test('Texto, ayuda y botones activos mantienen contraste de 4.5:1 en ambos temas', () => {
  for (const [mode, palette] of Object.entries(PALETTES)) {
    for (const foreground of [palette.text, palette.muted, palette.blue]) {
      for (const background of [palette.card, palette.background]) assert.ok(contrast(foreground, background) >= 4.5, `${mode}: ${foreground} sobre ${background}`);
    }
    assert.ok(contrast(palette.white, palette.primary) >= 4.5, `${mode}: botón principal`);
  }
});
test('Cada badge usa una superficie semántica legible en claro y oscuro', () => {
  const tones: Tone[] = ['blue', 'accent', 'violet', 'green', 'warning', 'danger', 'muted'];
  for (const [mode, palette] of Object.entries(PALETTES)) {
    for (const tone of tones) {
      const colors = toneColors(palette, tone);
      assert.ok(contrast(colors.foreground, colors.background) >= 4.5, `${mode}: ${tone}`);
    }
  }
});
test('Bordes de campos y foco mantienen contraste de 3:1 con su superficie', () => {
  for (const [mode, palette] of Object.entries(PALETTES)) {
    assert.ok(contrast(palette.fieldBorder, palette.card) >= 3, `${mode}: borde`);
    assert.ok(contrast(palette.blue, palette.card) >= 3, `${mode}: foco`);
  }
});
