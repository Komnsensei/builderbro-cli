// signature.mjs — soul signature: a deterministic visual identity computed
// from how you actually live. Same patterns → same signature. It changes
// slowly, only as your life does. You can't claim one; you earn it by living.

import { createHash } from "node:crypto";

const GLYPHS = ["◐", "◑", "◒", "◓", "◇", "◆", "○", "●", "△", "▲", "✶", "✺", "❋", "❂", "✧", "✦"];
const PALETTES = [
  ["#f4a261", "#e76f51", "#264653"],
  ["#9b5de5", "#f15bb5", "#fee440"],
  ["#06d6a0", "#118ab2", "#073b4c"],
  ["#ff6b6b", "#ffd93d", "#6bcb77"],
  ["#a8dadc", "#457b9d", "#1d3557"],
];
const NAMES = ["dawn", "ember", "moss", "tide", "cinder", "dusk", "drift", "hush", "bloom", "flux"];

// history: [{ at: ISO or ms, activity: "sleeping"|"active"|"still"|"walking"|"transit" }]
export function signatureFor(history = []) {
  // Bucket activity weight by hour-of-day → a 24-slot fingerprint.
  const slots = new Array(24).fill(0);
  for (const h of history) {
    const t = h.at instanceof Date ? h.at : new Date(h.at);
    const hour = Number.isNaN(t.getTime()) ? 0 : t.getHours();
    const weight = h.activity === "sleeping" ? 1 : h.activity === "active" ? 3 : 2;
    slots[hour] += weight;
  }

  const digest = createHash("sha256").update(JSON.stringify(slots)).digest();
  const palette = PALETTES[digest[0] % PALETTES.length];

  // One glyph per active slot, chosen deterministically.
  const glyphs = [];
  for (let i = 0; i < 24; i++) {
    if (slots[i] > 0) glyphs.push({ hour: i, glyph: GLYPHS[(digest[i % 16] + i) % GLYPHS.length] });
  }
  if (glyphs.length === 0) glyphs.push({ hour: 12, glyph: "◌" });

  const name = `${NAMES[digest[1] % NAMES.length]}-${NAMES[(digest[2] + 3) % NAMES.length]}`;
  return { slots, glyphs, palette, name };
}

// Stable test helper: derive a signature from repeated samples of the same life.
export function signatureForMember(history, { cap = 500 } = {}) {
  return signatureFor(history.slice(-cap));
}