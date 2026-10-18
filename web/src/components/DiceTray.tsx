// M11 D3: a D&D Beyond–style dice tray. Click dice to add them (right-click to take one
// away), pick who is rolling and which of their stats to add; the server adds the bonus.
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { message, useCampaign, useLoad } from "../hooks";
import { DiceIcon } from "../icons";
import { ABILITY_KEYS, ABILITY_NAMES, signed, skillName } from "../sheetRules";
import type { Derived, Roll, RollMode, SheetData } from "../types";
import { DieImage } from "./DieImage";

export const TRAY_DICE = [4, 6, 8, 10, 12, 20, 100] as const;
export const ROLL_EVENT = "dungeonmind:roll"; // the game log listens for rolls made here

interface StatOption {
  value: string;
  label: string;
}

export function statOptions(sheet: Partial<SheetData> | undefined, d: Derived): StatOption[] {
  const opts: StatOption[] = [{ value: "initiative", label: `Initiative ${signed(d.initiative)}` }];
  for (const a of ABILITY_KEYS) opts.push({ value: `check:${a}`, label: `${ABILITY_NAMES[a]} check ${signed(d.modifiers[a])}` });
  for (const a of ABILITY_KEYS) opts.push({ value: `save:${a}`, label: `${ABILITY_NAMES[a]} save ${signed(d.saves[a])}` });
  for (const [k, v] of Object.entries(d.skills)) opts.push({ value: `skill:${k}`, label: `${skillName(k)} ${signed(v)}` });
  (sheet?.attacks ?? []).forEach((a, i) => {
    if (d.attacks[i]) opts.push({ value: `attack:${i}`, label: `${a.name} attack ${signed(d.attacks[i].to_hit)}` });
  });
  if (d.spell_attack !== null) opts.push({ value: "spell_attack", label: `Spell attack ${signed(d.spell_attack)}` });
  return opts;
}

export function DiceTray() {
  const { campaign, user } = useCampaign();
  const [open, setOpen] = useState(false);
  const [dice, setDice] = useState<Record<number, number>>({});
  const [who, setWho] = useState("");
  const [stat, setStat] = useState("");
  const [modifier, setModifier] = useState(0);
  const [mode, setMode] = useState<RollMode>("normal");
  const [toDm, setToDm] = useState(false);
  const [result, setResult] = useState<Roll | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const { data: characters } = useLoad(
    () => (open ? api.characters(campaign.id) : Promise.resolve(undefined)),
    [campaign.id, open],
  );
  const choices = (characters ?? []).filter((c) => campaign.role === "dm" || c.owner_id === user.id);
  const chosen = choices.find((c) => String(c.id) === who);
  // Tagged with the character it belongs to, so a slow load never shows (or sends) another
  // character's stats (review #11).
  const { data: loaded } = useLoad(
    () =>
      chosen
        ? api.sheet(campaign.id, chosen.id).then((v) => ({ id: chosen.id, view: v }))
        : Promise.resolve(undefined),
    [campaign.id, chosen?.id],
  );
  const view = loaded && chosen && loaded.id === chosen.id ? loaded.view : undefined;
  const stats = useMemo(() => (view ? statOptions(view.sheet, view.derived) : []), [view]);
  const panelId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const firstDie = useRef<HTMLButtonElement>(null);
  const close = () => {
    setOpen(false);
    toggleRef.current?.focus(); // focus goes back where it came from
  };
  useEffect(() => {
    if (open) firstDie.current?.focus();
  }, [open]);

  const count = Object.values(dice).reduce((a, b) => a + b, 0);
  const loneD20 = count === 1 && dice[20] === 1;
  useEffect(() => {
    if (!loneD20 && mode !== "normal") setMode("normal");
  }, [loneD20, mode]);

  const add = (sides: number, step: number) =>
    setDice((old) => {
      const n = Math.max(0, Math.min(20, (old[sides] ?? 0) + step));
      const next = { ...old, [sides]: n };
      if (!n) delete next[sides];
      return next;
    });

  const pickWho = (value: string) => {
    setWho(value);
    setStat("");
    const c = choices.find((x) => String(x.id) === value);
    setToDm(Boolean(c?.is_npc)); // NPC rolls stay secret unless the DM says otherwise
  };

  const roll = async () => {
    if (!count) return;
    setBusy(true);
    setError(null);
    try {
      const body = {
        kind: "pool",
        dice: Object.fromEntries(Object.entries(dice).map(([k, v]) => [k, v])),
        modifier,
        mode,
        to_dm: toDm,
        ...(chosen ? { character_id: chosen.id } : {}),
        ...(chosen && stat ? { stat } : {}),
      };
      const made = await api.roll(campaign.id, body);
      setResult(made);
      window.dispatchEvent(new CustomEvent(ROLL_EVENT, { detail: made }));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`dice-tray${open ? " open" : ""}`}>
      {open && (
        <section
          id={panelId}
          className="tray-panel"
          role="region"
          aria-label="Dice tray"
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              close();
            }
          }}
        >
          <header className="tray-head">
            <h2>Dice</h2>
            <button type="button" className="icon-button" aria-label="Close dice tray" onClick={close}>
              ×
            </button>
          </header>
          <div className="tray-dice">
            {TRAY_DICE.map((d, i) => (
              <span key={d} className="tray-slot">
                <button
                  ref={i === 0 ? firstDie : undefined}
                  type="button"
                  className={`tray-die${dice[d] ? " picked" : ""}`}
                  aria-label={dice[d] ? `d${d}, ${dice[d]} selected` : `d${d}`}
                  onClick={() => add(d, 1)}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    add(d, -1);
                  }}
                >
                  <DieImage sides={d} />
                  {dice[d] ? <span className="tray-count" aria-hidden="true">{dice[d]}</span> : null}
                </button>
                {dice[d] ? (
                  <button type="button" className="tray-remove" aria-label={`Remove one d${d}`} onClick={() => add(d, -1)}>
                    −
                  </button>
                ) : null}
              </span>
            ))}
          </div>
          <p className="muted small">Click to add · − or right-click to remove{count ? ` · ${count} dice` : ""}</p>

          <div className="tray-row">
            <label>
              Roll as
              <select aria-label="Roll as" value={who} onChange={(e) => pickWho(e.target.value)}>
                <option value="">Me ({user.username})</option>
                {choices.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                    {c.is_npc ? " (NPC)" : ""}
                  </option>
                ))}
              </select>
            </label>
            {chosen && (
              <label>
                Add
                <select aria-label="Add" value={stat} disabled={!view} onChange={(e) => setStat(e.target.value)}>
                  <option value="">nothing</option>
                  {stats.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>

          <div className="tray-row">
            <div className="stepper" role="group" aria-label="Modifier">
              <button type="button" className="icon-button" aria-label="Decrease modifier" onClick={() => setModifier((m) => Math.max(-50, m - 1))}>
                −
              </button>
              <span className="stepper-value">{signed(modifier)}</span>
              <button type="button" className="icon-button" aria-label="Increase modifier" onClick={() => setModifier((m) => Math.min(50, m + 1))}>
                +
              </button>
            </div>
            <div role="radiogroup" aria-label="Roll mode" className="seg-toggle">
              {(["normal", "advantage", "disadvantage"] as const).map((m) => (
                <label key={m} className={mode === m ? "active" : undefined}>
                  <input
                    type="radio"
                    name="tray-mode"
                    value={m}
                    checked={mode === m}
                    disabled={m !== "normal" && !loneD20}
                    onChange={() => setMode(m)}
                  />
                  {m === "normal" ? "Normal" : m === "advantage" ? "Advantage" : "Disadvantage"}
                </label>
              ))}
            </div>
          </div>
          <label className="check">
            <input type="checkbox" checked={toDm} onChange={(e) => setToDm(e.target.checked)} /> to DM only
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="tray-actions">
            <button type="button" className="btn btn-ghost" onClick={() => { setDice({}); setModifier(0); setStat(""); }}>
              Clear
            </button>
            <button type="button" className="btn btn-primary" disabled={!count || busy} onClick={roll}>
              Roll {count ? `${count} ${count === 1 ? "die" : "dice"}` : ""}
            </button>
          </div>
          {result && (
            <div className="tray-result" role="status">
              <span className="tray-result-label">{result.label}</span>
              <span className="tray-result-dice">
                {result.terms.flatMap((t, i) => {
                  const sides = Number(t.term.split("d")[1]?.split("k")[0]);
                  const keep = [...t.kept]; // each kept value marks one die, so ties are right
                  return t.rolls.map((r, j) => {
                    const k = keep.indexOf(r);
                    if (k >= 0) keep.splice(k, 1);
                    return (
                      <span key={`${i}-${j}`} className={k >= 0 ? undefined : "dropped"} title={k >= 0 ? undefined : "dropped"}>
                        <DieImage sides={sides} size={42} value={r} />
                      </span>
                    );
                  });
                })}
                {result.bonus ? <span className="tray-bonus">{signed(result.bonus)}</span> : null}
              </span>
              <span className="tray-result-total">{result.total}</span>
            </div>
          )}
        </section>
      )}
      <button
        ref={toggleRef}
        type="button"
        className="tray-toggle btn btn-primary"
        aria-label="Dice tray"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <DiceIcon />
      </button>
    </div>
  );
}
