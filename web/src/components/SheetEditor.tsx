// M10c: the full sheet form (player and DM). The server validates and computes everything else.
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { ABILITY_KEYS, ABILITY_NAMES, SKILL_ABILITY, skillName } from "../sheetRules";
import type { AbilityKey, SheetData, SkillLevel } from "../types";

const CONDITIONS = [
  "blinded", "charmed", "deafened", "frightened", "grappled", "incapacitated", "invisible",
  "paralyzed", "petrified", "poisoned", "prone", "restrained", "stunned", "unconscious",
];

function Num({
  label,
  value,
  onChange,
  min = 0,
  max = 9999,
  step = 1,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  // Keeps what is typed ("", "-") until it's a number, so fields can be cleared and negative
  // numbers typed (review: an empty field used to snap to 0, so "-2" became "02").
  const [text, setText] = useState(String(value));
  useEffect(() => {
    if (Number(text) !== value) setText(String(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <label className="num-field">
      {label}
      <input
        type="number"
        value={text}
        min={min}
        max={max}
        step={step}
        onChange={(e) => {
          setText(e.target.value);
          const n = Number(e.target.value);
          if (e.target.value !== "" && Number.isFinite(n)) onChange(n);
        }}
        onBlur={() => {
          if (text === "" || !Number.isFinite(Number(text))) {
            setText(String(value));
          }
        }}
      />
    </label>
  );
}

function Txt({ label, value, onChange, max = 60 }: { label: string; value: string; onChange: (s: string) => void; max?: number }) {
  return (
    <label>
      {label}
      <input value={value} maxLength={max} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function Rows<T>({
  title,
  items,
  blank,
  onChange,
  max,
  render,
}: {
  title: string;
  items: T[];
  blank: T;
  onChange: (items: T[]) => void;
  max: number;
  render: (item: T, set: (patch: Partial<T>) => void, i: number) => ReactNode;
}) {
  return (
    <fieldset className="rows">
      <legend>{title}</legend>
      {items.map((item, i) => (
        <div key={i} className="row">
          {render(item, (patch) => onChange(items.map((x, j) => (j === i ? { ...x, ...patch } : x))), i)}
          <button type="button" className="btn btn-small btn-ghost danger" aria-label={`Remove ${title.toLowerCase()} ${i + 1}`} onClick={() => onChange(items.filter((_, j) => j !== i))}>
            Remove
          </button>
        </div>
      ))}
      {items.length < max && (
        <button type="button" className="btn btn-small" onClick={() => onChange([...items, blank])}>
          + Add
        </button>
      )}
    </fieldset>
  );
}

export function SheetEditor({
  sheet,
  error,
  onSave,
  onCancel,
}: {
  sheet: SheetData;
  error: string | null;
  onSave: (sheet: SheetData) => void | Promise<void>;
  onCancel: () => void;
}) {
  const [s, setS] = useState<SheetData>(() => structuredClone(sheet));
  const [busy, setBusy] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus(); // focus moves into the editor
  }, []);
  const set = (patch: Partial<SheetData>) => setS((old) => ({ ...old, ...patch }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await onSave(s);
    } finally {
      setBusy(false);
    }
  };

  const slots = Array.from({ length: 9 }, (_, i) => s.spellcasting.slots[i] ?? { max: 0, used: 0 });
  const setSlot = (i: number, patch: Partial<{ max: number; used: number }>) => {
    const next = slots.map((x, j) => (j === i ? { ...x, ...patch } : x));
    while (next.length && next[next.length - 1].max === 0) next.pop(); // trailing empty levels
    set({ spellcasting: { ...s.spellcasting, slots: next.map((x) => ({ ...x, used: Math.min(x.used, x.max) })) } });
  };

  return (
    <form className="sheet-editor" onSubmit={submit}>
      <header className="sheet-head">
        <h1 ref={heading} tabIndex={-1}>
          Edit sheet
        </h1>
        <p className="muted">Bonuses, saves and skills are worked out for you from these numbers.</p>
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <Rows
        title="Classes"
        items={s.classes}
        blank={{ name: "", subclass: "", level: 1, hit_die: 8 }}
        max={6}
        onChange={(classes) => set({ classes })}
        render={(c, setC) => (
          <>
            <Txt label="Class" value={c.name} onChange={(name) => setC({ name })} />
            <Txt label="Subclass" value={c.subclass} onChange={(subclass) => setC({ subclass })} />
            <Num label="Level" value={c.level} min={1} max={20} onChange={(level) => setC({ level })} />
            <label>
              Hit die
              <select value={c.hit_die} onChange={(e) => setC({ hit_die: Number(e.target.value) })}>
                {[6, 8, 10, 12].map((d) => (
                  <option key={d} value={d}>
                    d{d}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
      />
      <fieldset className="grid-fields">
        <legend>Identity</legend>
        <Txt label="Species" value={s.species} onChange={(species) => set({ species })} />
        <Txt label="Background" value={s.background} onChange={(background) => set({ background })} />
        <Txt label="Alignment" value={s.alignment} max={40} onChange={(alignment) => set({ alignment })} />
        <Num label="XP" value={s.xp} max={10_000_000} onChange={(xp) => set({ xp })} />
      </fieldset>

      <fieldset className="grid-fields">
        <legend>Ability scores and saves</legend>
        {ABILITY_KEYS.map((a) => (
          <div key={a} className="ability-edit">
            <Num label={ABILITY_NAMES[a]} value={s.abilities[a]} min={1} max={30} onChange={(n) => set({ abilities: { ...s.abilities, [a]: n } })} />
            <label className="check">
              <input
                type="checkbox"
                checked={s.saves.includes(a)}
                onChange={(e) => set({ saves: e.target.checked ? [...s.saves, a] : s.saves.filter((x) => x !== a) })}
              />{" "}
              Save proficiency
            </label>
          </div>
        ))}
      </fieldset>

      <fieldset className="grid-fields skills-edit">
        <legend>Skills</legend>
        {Object.keys(SKILL_ABILITY).map((k) => (
          <label key={k}>
            {skillName(k)}
            <select
              value={s.skills[k] ?? "none"}
              onChange={(e) => {
                const skills = { ...s.skills };
                if (e.target.value === "none") delete skills[k];
                else skills[k] = e.target.value as SkillLevel;
                set({ skills });
              }}
            >
              <option value="none">—</option>
              <option value="proficient">Proficient</option>
              <option value="expertise">Expertise</option>
            </select>
          </label>
        ))}
        <label className="check">
          <input type="checkbox" checked={s.jack_of_all_trades} onChange={(e) => set({ jack_of_all_trades: e.target.checked })} /> Jack of All Trades
        </label>
      </fieldset>

      <fieldset className="grid-fields">
        <legend>Combat</legend>
        <Num label="Armor Class" value={s.armor_class} max={50} onChange={(armor_class) => set({ armor_class })} />
        <Num label="Speed (ft)" value={s.speed} max={300} step={5} onChange={(speed) => set({ speed })} />
        <Num label="Initiative bonus (extra)" value={s.initiative_misc} min={-20} max={20} onChange={(initiative_misc) => set({ initiative_misc })} />
        <Num label="HP max" value={s.hp.max} max={2000} onChange={(max) => set({ hp: { ...s.hp, max } })} />
        <Num label="HP current" value={s.hp.current} max={2000} onChange={(current) => set({ hp: { ...s.hp, current } })} />
        <Num label="Temp HP" value={s.hp.temp} max={2000} onChange={(temp) => set({ hp: { ...s.hp, temp } })} />
        <Num label="Exhaustion" value={s.exhaustion} max={6} onChange={(exhaustion) => set({ exhaustion })} />
        <Num label="Death save successes" value={s.death_saves.successes} max={3} onChange={(successes) => set({ death_saves: { ...s.death_saves, successes } })} />
        <Num label="Death save failures" value={s.death_saves.failures} max={3} onChange={(failures) => set({ death_saves: { ...s.death_saves, failures } })} />
        <label className="check">
          <input type="checkbox" checked={s.inspiration} onChange={(e) => set({ inspiration: e.target.checked })} /> Heroic Inspiration
        </label>
      </fieldset>
      <fieldset className="grid-fields conditions-edit">
        <legend>Conditions</legend>
        {CONDITIONS.map((c) => (
          <label key={c} className="check">
            <input
              type="checkbox"
              checked={s.conditions.includes(c)}
              onChange={(e) => set({ conditions: e.target.checked ? [...s.conditions, c] : s.conditions.filter((x) => x !== c) })}
            />{" "}
            {c}
          </label>
        ))}
      </fieldset>

      <Rows
        title="Attacks"
        items={s.attacks}
        max={30}
        blank={{ name: "", ability: "str", proficient: true, to_hit_misc: 0, damage: "1d6", add_ability_to_damage: true, damage_type: "", notes: "" }}
        onChange={(attacks) => set({ attacks })}
        render={(a, setA) => (
          <>
            <Txt label="Name" value={a.name} onChange={(name) => setA({ name })} />
            <label>
              Ability
              <select value={a.ability ?? ""} onChange={(e) => setA({ ability: (e.target.value || null) as AbilityKey | null })}>
                <option value="">none</option>
                {ABILITY_KEYS.map((k) => (
                  <option key={k} value={k}>
                    {k.toUpperCase()}
                  </option>
                ))}
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={a.proficient} onChange={(e) => setA({ proficient: e.target.checked })} /> Proficient
            </label>
            <Num label="Extra to hit" value={a.to_hit_misc} min={-20} max={20} onChange={(to_hit_misc) => setA({ to_hit_misc })} />
            <Txt label="Damage dice" value={a.damage} max={40} onChange={(damage) => setA({ damage })} />
            <label className="check">
              <input type="checkbox" checked={a.add_ability_to_damage} onChange={(e) => setA({ add_ability_to_damage: e.target.checked })} /> + ability to damage
            </label>
            <Txt label="Damage type" value={a.damage_type} max={20} onChange={(damage_type) => setA({ damage_type })} />
          </>
        )}
      />

      <fieldset className="grid-fields">
        <legend>Spellcasting</legend>
        <label>
          Spellcasting ability
          <select
            value={s.spellcasting.ability ?? ""}
            onChange={(e) => set({ spellcasting: { ...s.spellcasting, ability: (e.target.value || null) as AbilityKey | null } })}
          >
            <option value="">none</option>
            {ABILITY_KEYS.map((k) => (
              <option key={k} value={k}>
                {ABILITY_NAMES[k]}
              </option>
            ))}
          </select>
        </label>
        {slots.map((slot, i) => (
          <Num key={i} label={`Level ${i + 1} slots`} value={slot.max} max={9} onChange={(max) => setSlot(i, { max })} />
        ))}
      </fieldset>
      <Rows
        title="Spells"
        items={s.spells}
        max={150}
        blank={{ name: "", level: 0, prepared: false, notes: "" }}
        onChange={(spells) => set({ spells })}
        render={(sp, setSp) => (
          <>
            <Txt label="Spell" value={sp.name} onChange={(name) => setSp({ name })} />
            <Num label="Level" value={sp.level} max={9} onChange={(level) => setSp({ level })} />
            <label className="check">
              <input type="checkbox" checked={sp.prepared} onChange={(e) => setSp({ prepared: e.target.checked })} /> Prepared
            </label>
            <Txt label="Notes" value={sp.notes} max={300} onChange={(notes) => setSp({ notes })} />
          </>
        )}
      />

      <Rows
        title="Inventory"
        items={s.inventory}
        max={150}
        blank={{ name: "", quantity: 1, weight: 0, equipped: false, notes: "" }}
        onChange={(inventory) => set({ inventory })}
        render={(it, setIt) => (
          <>
            <Txt label="Item" value={it.name} onChange={(name) => setIt({ name })} />
            <Num label="Qty" value={it.quantity} max={9999} onChange={(quantity) => setIt({ quantity })} />
            <Num label="Weight (lb)" value={it.weight} max={1000} step={0.25} onChange={(weight) => setIt({ weight })} />
            <label className="check">
              <input type="checkbox" checked={it.equipped} onChange={(e) => setIt({ equipped: e.target.checked })} /> Equipped
            </label>
            <Txt label="Notes" value={it.notes} max={300} onChange={(notes) => setIt({ notes })} />
          </>
        )}
      />
      <fieldset className="grid-fields">
        <legend>Currency</legend>
        {(["pp", "gp", "ep", "sp", "cp"] as const).map((k) => (
          <Num key={k} label={k.toUpperCase()} value={s.currency[k]} max={10_000_000} onChange={(n) => set({ currency: { ...s.currency, [k]: n } })} />
        ))}
      </fieldset>

      <Rows
        title="Features and traits"
        items={s.features}
        max={60}
        blank={{ name: "", source: "", text: "" }}
        onChange={(features) => set({ features })}
        render={(f, setF) => (
          <>
            <Txt label="Feature" value={f.name} max={80} onChange={(name) => setF({ name })} />
            <Txt label="Source" value={f.source} onChange={(source) => setF({ source })} />
            <label className="wide">
              Description
              <textarea value={f.text} maxLength={2000} rows={3} onChange={(e) => setF({ text: e.target.value })} />
            </label>
          </>
        )}
      />

      <Rows
        title="Custom dice"
        items={s.custom_dice}
        max={30}
        blank={{ name: "", expression: "1d6", notes: "" }}
        onChange={(custom_dice) => set({ custom_dice })}
        render={(c, setC) => (
          <>
            <Txt label="Name" value={c.name} max={40} onChange={(name) => setC({ name })} />
            <Txt label="Dice (e.g. 3d6+2)" value={c.expression} max={40} onChange={(expression) => setC({ expression })} />
            <Txt label="Notes" value={c.notes} max={200} onChange={(notes) => setC({ notes })} />
          </>
        )}
      />

      <fieldset>
        <legend>Notes</legend>
        <label>
          Proficiencies and languages
          <textarea value={s.proficiencies} maxLength={2000} rows={3} onChange={(e) => set({ proficiencies: e.target.value })} />
        </label>
        <label>
          Notes
          <textarea value={s.notes} maxLength={5000} rows={5} onChange={(e) => set({ notes: e.target.value })} />
        </label>
      </fieldset>
      <div className="actions sticky-actions">
        {error && (
          <p className="error sticky-error" aria-hidden="true">
            {error}
          </p>
        )}
        <button type="button" className="btn btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn btn-primary" disabled={busy}>
          {busy ? "Saving…" : "Save sheet"}
        </button>
      </div>
    </form>
  );
}
