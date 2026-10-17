// Shared sheet vocabulary for the sheet page and its editor (mirrors hub/sheets.py).
import type { AbilityKey } from "./types";

export const ABILITY_NAMES: Record<AbilityKey, string> = {
  str: "Strength",
  dex: "Dexterity",
  con: "Constitution",
  int: "Intelligence",
  wis: "Wisdom",
  cha: "Charisma",
};
export const ABILITY_KEYS = Object.keys(ABILITY_NAMES) as AbilityKey[];
export const SKILL_ABILITY: Record<string, AbilityKey> = {
  acrobatics: "dex", animal_handling: "wis", arcana: "int", athletics: "str", deception: "cha",
  history: "int", insight: "wis", intimidation: "cha", investigation: "int", medicine: "wis",
  nature: "int", perception: "wis", performance: "cha", persuasion: "cha", religion: "int",
  sleight_of_hand: "dex", stealth: "dex", survival: "wis",
};

export const signed = (n: number) => `${n >= 0 ? "+" : "-"}${Math.abs(n)}`;
export const skillName = (key: string) =>
  key.split("_").map((w, i) => (i > 0 && w === "of" ? w : w[0].toUpperCase() + w.slice(1))).join(" ");

