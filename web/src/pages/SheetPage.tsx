// M10c: a D&D Beyond–style character sheet. Every bonus is a roll button for the player and the
// DM (the server computes the bonus from the stored sheet); other members see it read-only.
import { type KeyboardEvent as ReactKeyboardEvent, createContext, useContext, useEffect, useId, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../api";
import { GameLog, rollDetail } from "../components/GameLog";
import { SheetEditor } from "../components/SheetEditor";
import { message, useCampaign, useLoad } from "../hooks";
import { ABILITY_KEYS, ABILITY_NAMES, SKILL_ABILITY, signed, skillName } from "../sheetRules";
import type { Roll, RollMode, SheetData, SheetView } from "../types";

interface Roller {
  canEdit: boolean;
  roll: (kind: string, key?: string, mode?: RollMode) => void;
}
const RollContext = createContext<Roller>({ canEdit: false, roll: () => undefined });

/** A bonus: a roll button for editors, plain text for everyone else (stable identity, so the
 * clicked button keeps keyboard focus across re-renders). */
function Bonus({
  label,
  value,
  kind,
  k,
  rollMode,
}: {
  label: string;
  value: number | string;
  kind: string;
  k?: string;
  rollMode?: RollMode;
}) {
  const { canEdit, roll } = useContext(RollContext);
  const text = typeof value === "number" ? signed(value) : value;
  return canEdit ? (
    <button type="button" className="roll-btn" aria-label={`${label} ${text}`} onClick={() => roll(kind, k, rollMode)}>
      {text}
    </button>
  ) : (
    <span className="roll-value">{text}</span>
  );
}

const TABS = ["Actions", "Spells", "Inventory", "Features", "Custom dice", "Notes"] as const;
type Tab = (typeof TABS)[number];

export function SheetPage() {
  const { campaign } = useCampaign();
  const { chid = "" } = useParams();
  const characterId = Number(chid);
  const { data: characters } = useLoad(() => api.characters(campaign.id), [campaign.id]);
  const { data: loaded, error: loadError } = useLoad(
    () => api.sheet(campaign.id, characterId),
    [campaign.id, characterId],
  );
  const [view, setView] = useState<SheetView | null>(null);
  const [mode, setMode] = useState<RollMode>("normal");
  const [toDm, setToDm] = useState(false);
  const [last, setLast] = useState<Roll | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  // The newest sheet and whether a save is in flight, for queued HP clicks (review: disabling
  // the ± buttons dropped keyboard focus, and clicks during a slow save were lost).
  const latest = useRef<SheetView | null>(null);
  const inFlight = useRef(false);
  const hpQueue = useRef(0);
  const editButton = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);
  useEffect(() => {
    if (wasEditing.current && !editing) editButton.current?.focus(); // back where it began
    wasEditing.current = editing;
  }, [editing]);
  const [tab, setTab] = useState<Tab>("Actions");
  const modeName = useId();
  const tabsId = useId();

  /** P5: ARIA tabs keys — arrows move and select, Home / End jump. */
  const onTabKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const i = TABS.indexOf(tab);
    const next =
      e.key === "ArrowRight" ? (i + 1) % TABS.length
      : e.key === "ArrowLeft" ? (i - 1 + TABS.length) % TABS.length
      : e.key === "Home" ? 0
      : e.key === "End" ? TABS.length - 1
      : -1;
    if (next < 0) return;
    e.preventDefault();
    setTab(TABS[next]);
    document.getElementById(`${tabsId}-tab-${next}`)?.focus();
  };

  useEffect(() => {
    if (loaded) {
      latest.current = loaded; // set with the view itself, never one effect later
      setView(loaded);
    }
  }, [loaded]);
  // P3: pick up a newer version someone else saved, unless we're editing or saving.
  useEffect(() => {
    const timer = setInterval(() => {
      if (editing || inFlight.current || hpQueue.current !== 0) return;
      if (typeof document !== "undefined" && document.hidden) return;
      api
        .sheet(campaign.id, characterId)
        .then((fresh) => {
          const current = latest.current;
          if (fresh && current && fresh.version > current.version && !inFlight.current) {
            latest.current = fresh;
            setView(fresh);
          }
        })
        .catch(() => undefined);
    }, 5000);
    return () => clearInterval(timer);
  }, [campaign.id, characterId, editing]);

  const character = characters?.find((c) => c.id === characterId);
  const name = character?.name ?? "Character";

  if (loadError) return <p className="error page" role="alert">{loadError}</p>;
  if (!view) return <p className="muted page">Loading sheet…</p>;

  const { sheet, derived: d, can_edit: canEdit } = view;

  const roll = async (kind: string, key?: string, rollMode?: RollMode) => {
    setError(null);
    try {
      const body = {
        character_id: characterId,
        kind,
        ...(key !== undefined ? { key } : {}),
        mode: rollMode ?? mode,
        to_dm: toDm,
      };
      setLast(await api.roll(campaign.id, body));
    } catch (e) {
      setError(message(e));
    }
  };

  const showView = (next: SheetView) => {
    latest.current = next;
    setView(next);
  };

  /** Saves `build(newest sheet)` against the newest version; one save at a time. A conflict
   * reloads the newest sheet and says so. */
  const save = async (build: (current: SheetData) => SheetData): Promise<boolean> => {
    const base = latest.current;
    if (!base || inFlight.current) return false;
    inFlight.current = true;
    setSaving(true);
    setError(null);
    try {
      showView(await api.saveSheet(campaign.id, characterId, build(base.sheet), base.version));
      return true;
    } catch (e) {
      hpQueue.current = 0;
      if (e instanceof ApiError && e.status === 409) {
        try {
          showView(await api.sheet(campaign.id, characterId));
          setError(
            "Someone else changed this sheet, so the newest version was reloaded. " +
              "Saving your edits again will replace their changes.",
          );
        } catch (reloadError) {
          setError(message(reloadError));
        }
      } else {
        setError(message(e));
      }
      return false;
    } finally {
      inFlight.current = false;
      setSaving(false);
    }
  };

  /** HP clicks add up while a save runs and are saved together right after it. */
  const flushHp = async () => {
    while (hpQueue.current !== 0 && !inFlight.current) {
      const delta = hpQueue.current;
      hpQueue.current = 0;
      const ok = await save((s) => ({
        ...s,
        hp: { ...s.hp, current: Math.min(s.hp.max, Math.max(0, s.hp.current + delta)) },
      }));
      if (!ok) break;
    }
  };
  const changeHp = (delta: number) => {
    hpQueue.current += delta;
    void flushHp();
  };
  const hp = sheet.hp;
  const classes = sheet.classes.map((c) => `${c.name}${c.subclass ? ` (${c.subclass})` : ""} ${c.level}`).join(" / ");

  return (
    <RollContext.Provider value={{ canEdit, roll: (kind, key, m) => void roll(kind, key, m) }}>
    <section className="sheet">
      <header className="sheet-head">
        <div>
          <p className="eyebrow">
            <Link to={`/c/${campaign.id}/characters/${characterId}`}>← Storyboard</Link>
          </p>
          <h1>{name}</h1>
          <p className="muted">
            {[classes || `Level ${d.level}`, sheet.species, sheet.background].filter(Boolean).join(" · ")}
            {" · "}Proficiency {signed(d.proficiency_bonus)}
          </p>
        </div>
        {canEdit && !editing && (
          <div className="sheet-controls">
            <div role="radiogroup" aria-label="Roll mode" className="seg-toggle">
              {(["normal", "advantage", "disadvantage"] as const).map((m) => (
                <label key={m} className={mode === m ? "active" : undefined}>
                  <input type="radio" name={modeName} value={m} checked={mode === m} onChange={() => setMode(m)} />
                  {m === "normal" ? "Normal" : m === "advantage" ? "Advantage" : "Disadvantage"}
                </label>
              ))}
            </div>
            <label className="check">
              <input type="checkbox" checked={toDm} onChange={(e) => setToDm(e.target.checked)} /> Roll to DM only
            </label>
            <button type="button" className="btn" ref={editButton} onClick={() => setEditing(true)}>
              Edit sheet
            </button>
          </div>
        )}
      </header>

      {error && !editing && <p className="error" role="alert">{error}</p>}
      {last && (
        <div className={`roll-result${last.natural === 20 ? " nat20" : last.natural === 1 ? " nat1" : ""}`} role="status">
          <span className="roll-result-label">{last.label}</span>
          <span className="roll-result-total">{last.total}</span>
          <span className="roll-result-detail">
            {rollDetail(last)}
            {last.natural === 20 ? " · natural 20" : last.natural === 1 ? " · natural 1" : ""}
            {last.outcome ? ` · ${last.outcome}` : ""}
          </span>
        </div>
      )}

      <div className="sheet-layout">
        <div className="sheet-main">
          {editing ? (
            <SheetEditor
              sheet={sheet}
              error={error}
              onCancel={() => {
                setEditing(false);
                setError(null);
              }}
              onSave={async (next) => {
                if (await save(() => next)) setEditing(false);
              }}
            />
          ) : (
          <>
          <div className="combat-strip">
            <div role="group" aria-label="Armor Class" className="combat-box">
              <span className="box-label">Armor Class</span>
              <span className="box-value">{sheet.armor_class}</span>
            </div>
            <div className="combat-box">
              <span className="box-label">Initiative</span>
              <Bonus label="Initiative" value={d.initiative} kind="initiative" />
            </div>
            <div className="combat-box">
              <span className="box-label">Speed</span>
              <span className="box-value">{d.speed} ft</span>
            </div>
            <div role="group" aria-label="Hit points" className="combat-box hp-box">
              <span className="box-label">Hit points</span>
              <span className="box-value">
                {hp.current}
                <small> / {hp.max}</small>
                {hp.temp > 0 && <small className="temp"> +{hp.temp} temp</small>}
              </span>
              {canEdit && (
                <span className="hp-buttons">
                  <button type="button" className="icon-button" aria-label="Lose 1 HP" onClick={() => changeHp(-1)}>
                    −
                  </button>
                  <button type="button" className="icon-button" aria-label="Gain 1 HP" onClick={() => changeHp(1)}>
                    +
                  </button>
                </span>
              )}
            </div>
            <div className="combat-box">
              <span className="box-label">Passive Perception</span>
              <span className="box-value">{d.passive_perception}</span>
            </div>
            {(sheet.inspiration || sheet.exhaustion > 0 || sheet.conditions.length > 0) && (
              <div className="combat-box status-box">
                {sheet.inspiration && <span className="log-tag">Heroic Inspiration</span>}
                {sheet.exhaustion > 0 && <span className="log-tag dm">Exhaustion {sheet.exhaustion}</span>}
                {sheet.conditions.map((c) => (
                  <span key={c} className="log-tag">{c}</span>
                ))}
              </div>
            )}
          </div>

          <div className="abilities">
            {ABILITY_KEYS.map((a) => (
              <div key={a} className="ability">
                <span className="ability-name">{ABILITY_NAMES[a]}</span>
                <Bonus label={`${ABILITY_NAMES[a]} check`} value={d.modifiers[a]} kind="check" k={a} />
                <span className="ability-score">{sheet.abilities[a]}</span>
                <span className="ability-save">
                  Save
                  {sheet.saves.includes(a) && (
                    <>
                      <span aria-hidden="true"> ●</span>
                      <span className="sr-only"> (proficient)</span>
                    </>
                  )}{" "}
                  <Bonus label={`${ABILITY_NAMES[a]} save`} value={d.saves[a]} kind="save" k={a} />
                </span>
              </div>
            ))}
          </div>

          <div className="sheet-cols">
            <section className="panel skills" aria-labelledby="skills-h">
              <h2 id="skills-h">Skills</h2>
              <ul>
                {Object.keys(SKILL_ABILITY).map((s) => {
                  const level = sheet.skills[s] ?? "none";
                  return (
                    <li key={s}>
                      <span className={`prof prof-${level}`} title={level} aria-hidden="true" />
                      {level !== "none" && <span className="sr-only">{level}</span>}
                      <span className="skill-name">
                        {skillName(s)} <small>({SKILL_ABILITY[s].toUpperCase()})</small>
                      </span>
                      <Bonus label={skillName(s)} value={d.skills[s]} kind="skill" k={s} />
                    </li>
                  );
                })}
              </ul>
            </section>

            <section className="panel sheet-tabs">
              <div role="tablist" aria-label="Sheet sections" className="tablist" onKeyDown={onTabKey}>
                {TABS.map((t, i) => (
                  <button
                    key={t}
                    id={`${tabsId}-tab-${i}`}
                    type="button"
                    role="tab"
                    aria-selected={tab === t}
                    aria-controls={`${tabsId}-panel`}
                    tabIndex={tab === t ? 0 : -1}
                    className={tab === t ? "tab active" : "tab"}
                    onClick={() => setTab(t)}
                  >
                    {t}
                  </button>
                ))}
              </div>
              <div
                role="tabpanel"
                id={`${tabsId}-panel`}
                aria-labelledby={`${tabsId}-tab-${TABS.indexOf(tab)}`}
                className="tabpanel"
                tabIndex={0}
              >
                {tab === "Actions" && (
                  <>
                    {sheet.attacks.length === 0 && <p className="muted">No attacks yet.</p>}
                    <table className="sheet-table">
                      <tbody>
                        {sheet.attacks.map((a, i) => (
                          <tr key={i}>
                            <th scope="row">{a.name}</th>
                            <td><Bonus label={`${a.name} attack`} value={d.attacks[i].to_hit} kind="attack" k={String(i)} /></td>
                            <td>
                              {d.attacks[i].damage && (
                                <Bonus label={`${a.name} damage`} value={d.attacks[i].damage} kind="damage" k={String(i)} rollMode="normal" />
                              )}{" "}
                              <small>{a.damage_type}</small>
                            </td>
                            <td>
                              {canEdit && d.attacks[i].damage && (
                                <button type="button" className="btn btn-small btn-ghost" aria-label={`${a.name} critical damage`} onClick={() => roll("damage", String(i), "critical")}>
                                  Crit
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="muted small">
                      Hit dice:{" "}
                      {Object.entries(d.hit_dice).map(([die, n]) => (
                        <span key={die} className="hit-die">
                          {n - (sheet.hit_dice_used[die] ?? 0)}/{n} d{die}{" "}
                          <Bonus label={`Hit die d${die}`} value={`d${die}`} kind="hit_die" k={die} rollMode="normal" />
                        </span>
                      ))}
                      {" · "}
                      <Bonus label="Death save" value="Death save" kind="death_save" />
                    </p>
                  </>
                )}
                {tab === "Spells" && (
                  <>
                    <p>
                      {d.spell_save_dc !== null ? (
                        <>
                          Spell save DC <strong>{d.spell_save_dc}</strong> · Spell attack{" "}
                          <Bonus label="Spell attack" value={d.spell_attack ?? 0} kind="spell_attack" />
                        </>
                      ) : (
                        <span className="muted">No spellcasting ability set.</span>
                      )}
                    </p>
                    {sheet.spellcasting.slots.some((s) => s.max > 0) && (
                      <div className="slots">
                        {sheet.spellcasting.slots.map((s, i) =>
                          s.max > 0 ? (
                            <div key={i} className="slot-row">
                              <span>Level {i + 1}</span>
                              {Array.from({ length: s.max }, (_, j) => (
                                <input
                                  key={j}
                                  type="checkbox"
                                  aria-label={`Level ${i + 1} slot ${j + 1} used`}
                                  checked={j < s.used}
                                  disabled={!canEdit}
                                  aria-disabled={saving || undefined}
                                  onChange={() => {
                                    if (saving) return; // keep focus; the click is ignored
                                    void save((cur) => ({
                                      ...cur,
                                      spellcasting: {
                                        ...cur.spellcasting,
                                        slots: cur.spellcasting.slots.map((x, k) => (k === i ? { ...x, used: j < s.used ? j : j + 1 } : x)),
                                      },
                                    }));
                                  }}
                                />
                              ))}
                            </div>
                          ) : null,
                        )}
                      </div>
                    )}
                    {[...Array(10).keys()].map((lvl) => {
                      const spells = sheet.spells.filter((s) => s.level === lvl);
                      return spells.length ? (
                        <div key={lvl}>
                          <h3>{lvl === 0 ? "Cantrips" : `Level ${lvl}`}</h3>
                          <ul className="plain-list">
                            {spells.map((s, i) => (
                              <li key={i}>
                                {s.name} {s.prepared && <span className="log-tag">prepared</span>}
                                {s.notes && <small className="muted"> — {s.notes}</small>}
                              </li>
                            ))}
                          </ul>
                        </div>
                      ) : null;
                    })}
                  </>
                )}
                {tab === "Inventory" && (
                  <>
                    <p className="muted">
                      Carrying {d.carried_weight} / {d.carrying_capacity} lb ·{" "}
                      {(["pp", "gp", "ep", "sp", "cp"] as const).map((k) => `${sheet.currency[k]} ${k}`).join(" · ")}
                    </p>
                    <table className="sheet-table">
                      <tbody>
                        {sheet.inventory.map((it, i) => (
                          <tr key={i}>
                            <th scope="row">{it.name}{it.equipped && <span className="log-tag">equipped</span>}</th>
                            <td>×{it.quantity}</td>
                            <td>{it.weight} lb</td>
                            <td className="muted">{it.notes}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
                {tab === "Features" && (
                  <>
                    {sheet.features.length === 0 && <p className="muted">No features yet.</p>}
                    {sheet.features.map((f, i) => (
                      <details key={i} className="feature">
                        <summary>
                          {f.name} {f.source && <small className="muted">· {f.source}</small>}
                        </summary>
                        <p>{f.text}</p>
                      </details>
                    ))}
                  </>
                )}
                {tab === "Custom dice" && (
                  <>
                    {sheet.custom_dice.length === 0 && <p className="muted">No custom dice yet. Add some in Edit sheet.</p>}
                    <div className="custom-dice">
                      {sheet.custom_dice.map((c, i) => (
                        <div key={i} className="custom-die">
                          <span className="custom-name">{c.name}</span>
                          <Bonus label={c.name} value={c.expression} kind="custom" k={String(i)} rollMode="normal" />
                          {c.notes && <small className="muted">{c.notes}</small>}
                        </div>
                      ))}
                    </div>
                  </>
                )}
                {tab === "Notes" && (
                  <>
                    {sheet.proficiencies && (
                      <>
                        <h3>Proficiencies and languages</h3>
                        <p className="pre">{sheet.proficiencies}</p>
                      </>
                    )}
                    {sheet.notes ? <p className="pre">{sheet.notes}</p> : <p className="muted">No notes.</p>}
                  </>
                )}
              </div>
            </section>
          </div>
          </>
          )}
        </div>
        <aside className="sheet-side">
          <GameLog cid={campaign.id} incoming={last} />
        </aside>
      </div>
    </section>
    </RollContext.Provider>
  );
}
