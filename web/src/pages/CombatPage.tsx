// M11 C6: the combat tracker. Polls every 3 s; the server decides what this viewer sees
// (players: NPC health states only, no hidden combatants).
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { message, useCampaign, useLoad } from "../hooks";
import type { Combatant, Encounter } from "../types";

const POLL_MS = 3000;
const CONDITIONS = [
  "blinded", "charmed", "deafened", "frightened", "grappled", "incapacitated", "invisible",
  "paralyzed", "petrified", "poisoned", "prone", "restrained", "stunned", "unconscious",
];
const HEALTH_LABEL = { unharmed: "Unharmed", injured: "Injured", bloodied: "Bloodied", down: "Down" };

function HpBar({ c }: { c: Combatant }) {
  if (!c.hp) return <span className={`health health-${c.health}`}>{HEALTH_LABEL[c.health]}</span>;
  const pct = c.hp.max ? Math.round((c.hp.current / c.hp.max) * 100) : 0;
  return (
    <span className="hp">
      <span className={`hp-bar health-${c.health}`} aria-hidden="true">
        <span style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
      </span>
      <span>
        {c.hp.current} / {c.hp.max}
        {c.hp.temp ? ` +${c.hp.temp}` : ""}
      </span>
    </span>
  );
}

export function CombatPage() {
  const { campaign } = useCampaign();
  const dm = campaign.role === "dm";
  const [enc, setEnc] = useState<Encounter | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [count, setCount] = useState(1);
  const [found, setFound] = useState<string[]>([]);
  const [amountText, setAmountText] = useState("1");
  const amount = Math.max(1, Math.min(2000, Math.trunc(Number(amountText)) || 1));
  const busy = useRef(false);
  const [pending, setPending] = useState(false); // one action at a time (no double moves)
  const seq = useRef(0); // a poll older than the newest action is ignored

  const load = () => {
    const mine = ++seq.current;
    return api
      .encounter(campaign.id)
      .then((e) => {
        if (mine !== seq.current) return;
        setEnc(e);
        setError(null);
      })
      .catch((e) => mine === seq.current && setError(message(e)));
  };

  useEffect(() => {
    void load();
    const timer = setInterval(() => {
      if (!busy.current && !(typeof document !== "undefined" && document.hidden)) void load();
    }, POLL_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaign.id]);

  const { data: characters } = useLoad(
    () => (dm && enc ? api.characters(campaign.id) : Promise.resolve([])),
    [campaign.id, dm, enc?.id],
  );

  useEffect(() => {
    if (!dm || query.trim().length < 2) {
      setFound([]);
      return;
    }
    const t = setTimeout(() => {
      api.monsters(campaign.id, query.trim()).then(setFound).catch(() => setFound([]));
    }, 250);
    return () => clearTimeout(t);
  }, [campaign.id, dm, query]);

  /** Runs one change at a time; uses the encounter it returns, or reloads. */
  const act = async (work: () => Promise<unknown>) => {
    if (busy.current) return;
    busy.current = true;
    setPending(true);
    seq.current++; // any poll in flight is now stale
    setError(null);
    try {
      const next = await work();
      if (next && typeof next === "object" && "combatants" in (next as object)) {
        seq.current++;
        setEnc(next as Encounter);
      } else await load();
    } catch (e) {
      setError(message(e));
    } finally {
      busy.current = false;
      setPending(false);
    }
  };

  // Screen readers hear whose turn it is when it changes (review #10).
  const current = enc?.combatants.find((c) => c.id === enc.current_id);
  const announcement = enc ? `Round ${enc.round}${current ? ` — ${current.name}'s turn` : ""}` : "";

  const start = (e: FormEvent) => {
    e.preventDefault();
    void act(() => api.startEncounter(campaign.id, name.trim() || "Encounter"));
  };

  if (enc === undefined) return <p className="muted page">Loading…</p>;

  if (enc === null) {
    return (
      <section className="board combat">
        <header className="board-head">
          <div>
            <h1>Combat</h1>
            <p className="muted">
              {dm ? "Start an encounter, add the party and monsters, and roll initiative." : "No fight right now."}
            </p>
          </div>
        </header>
        {error && <p className="error" role="alert">{error}</p>}
        {dm && (
          <form className="panel start-encounter" onSubmit={start}>
            <label>
              Encounter name
              <input aria-label="Encounter name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="e.g. Ambush on the road" />
            </label>
            <button className="btn btn-primary">Start encounter</button>
          </form>
        )}
      </section>
    );
  }

  const inFight = new Set(enc.combatants.map((c) => c.character_id));
  const available = (characters ?? []).filter((c) => !inFight.has(c.id));

  return (
    <section className="board combat">
      <header className="board-head">
        <div>
          <h1>{enc.name}</h1>
          <p className="muted">Round {enc.round}</p>
        </div>
        {dm && (
          <div className="actions">
            <button type="button" className="btn" disabled={pending} onClick={() => act(() => api.turn(campaign.id, enc.id, "prev"))}>
              Previous
            </button>
            <button type="button" className="btn btn-primary" disabled={pending} onClick={() => act(() => api.turn(campaign.id, enc.id, "next"))}>
              Next turn
            </button>
          </div>
        )}
      </header>
      {error && <p className="error" role="alert">{error}</p>}

      <p className="sr-only" aria-live="polite">{announcement}</p>
      <div className="combat-tools">
        {dm && (
          <button type="button" className="btn" disabled={pending} onClick={() => act(() => api.rollInitiative(campaign.id, enc.id, true))}>
            Roll initiative
          </button>
        )}
        {enc.combatants.some((c) => c.can_edit) && (
          <label className="num-field amount">
            HP change
            <input
              type="number"
              min={1}
              max={2000}
              value={amountText}
              onChange={(e) => setAmountText(e.target.value)}
              onBlur={() => setAmountText(String(amount))}
            />
          </label>
        )}
        {dm && (
          <button
            type="button"
            className="btn btn-ghost danger end-encounter"
            disabled={pending}
            onClick={() => {
              if (!window.confirm(`End “${enc.name}”?`)) return;
              const remove = window.confirm(
                "Also delete the monsters that were added for this fight? (Cancel keeps them as NPCs.)",
              );
              void act(() => api.endEncounter(campaign.id, enc.id, remove));
            }}
          >
            End encounter
          </button>
        )}
      </div>

      <ol className="initiative-list">
        {enc.combatants.map((c) => (
          <li key={c.id} aria-current={c.id === enc.current_id ? "true" : undefined} className={`combatant${c.id === enc.current_id ? " current" : ""}${c.hidden ? " hidden-combatant" : ""}`}>
            <span className="init" aria-label={c.initiative === null ? "no initiative yet" : `initiative ${c.initiative}`}>{c.initiative ?? "–"}</span>
            <span className="combatant-name">
              {c.name}
              {c.is_npc && <small className="muted"> NPC</small>}
              {c.hidden && <small className="log-tag dm">hidden</small>}
              {c.character_id && !c.is_npc && (
                <Link className="small" to={`/c/${campaign.id}/characters/${c.character_id}/sheet`}>
                  sheet
                </Link>
              )}
            </span>
            {c.ac !== undefined && <span className="ac" title="Armor Class">AC {c.ac}</span>}
            <HpBar c={c} />
            <span className="conditions">
              {c.conditions.map((x) =>
                c.can_edit ? (
                  <button
                    key={x}
                    type="button"
                    className="log-tag tag-button"
                    aria-label={`Remove ${x} from ${c.name}`}
                    disabled={pending}
                    onClick={() => act(() => api.combatConditions(campaign.id, enc.id, c.id, c.conditions.filter((y) => y !== x)))}
                  >
                    {x} ×
                  </button>
                ) : (
                  <span key={x} className="log-tag">{x}</span>
                ),
              )}
            </span>
            {c.can_edit && (
              <span className="combatant-actions">
                <button type="button" className="btn btn-small" aria-label={`Damage ${c.name} by ${amount}`} disabled={pending} onClick={() => act(() => api.combatHp(campaign.id, enc.id, c.id, -amount))}>
                  −{amount}
                </button>
                <button type="button" className="btn btn-small" aria-label={`Heal ${c.name} by ${amount}`} disabled={pending} onClick={() => act(() => api.combatHp(campaign.id, enc.id, c.id, amount))}>
                  +{amount}
                </button>
                <select
                  aria-label={`Add condition to ${c.name}`}
                  value=""
                  disabled={pending}
                  onChange={(e) => e.target.value && act(() => api.combatConditions(campaign.id, enc.id, c.id, [...c.conditions, e.target.value]))}
                >
                  <option value="">+ condition</option>
                  {CONDITIONS.filter((x) => !c.conditions.includes(x)).map((x) => (
                    <option key={x} value={x}>{x}</option>
                  ))}
                </select>

                {dm && (
                  <>
                    <button type="button" className="btn btn-small btn-ghost" disabled={pending} onClick={() => act(() => api.updateCombatant(campaign.id, enc.id, c.id, { hidden: !c.hidden }))}>
                      {c.hidden ? "Reveal" : "Hide"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-small btn-ghost"
                      onClick={() => {
                        const v = window.prompt(`Initiative for ${c.name}`, String(c.initiative ?? ""));
                        if (v === null || v.trim() === "") return;
                        if (!Number.isInteger(Number(v))) {
                          setError("Initiative must be a whole number.");
                          return;
                        }
                        void act(() => api.updateCombatant(campaign.id, enc.id, c.id, { initiative: Number(v) }));
                      }}
                    >
                      Set initiative
                    </button>
                    <button type="button" className="btn btn-small btn-ghost danger" aria-label={`Remove ${c.name}`} disabled={pending} onClick={() => act(() => api.removeCombatant(campaign.id, enc.id, c.id))}>
                      ×
                    </button>
                  </>
                )}
              </span>
            )}
          </li>
        ))}
      </ol>
      {enc.combatants.length === 0 && <p className="empty">No one in this fight yet.</p>}

      {dm && (
        <section className="panel add-combatants" aria-labelledby="add-h">
          <h2 id="add-h">Add to the fight</h2>
          {available.length > 0 && (
            <div className="chip-row">
              {available.map((c) => (
                <button key={c.id} type="button" className="chip" disabled={pending} onClick={() => act(() => api.addCombatants(campaign.id, enc.id, { character_id: c.id }))}>
                  + {c.name}{c.is_npc ? " (NPC)" : ""}
                </button>
              ))}
            </div>
          )}
          <div className="monster-search">
            <label>
              Find a monster
              <input aria-label="Find a monster" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="SRD monsters, e.g. goblin" />
            </label>
            <label className="num-field">
              How many
              <input type="number" min={1} max={10} value={count} onChange={(e) => setCount(Math.max(1, Math.min(10, Number(e.target.value) || 1)))} />
            </label>
          </div>
          <div className="chip-row">
            {found.map((m) => (
              <button key={m} type="button" className="chip" aria-label={`Add ${m}`} disabled={pending} onClick={() => act(() => api.addCombatants(campaign.id, enc.id, { monster: m, count }))}>
                + {m}{count > 1 ? ` ×${count}` : ""}
              </button>
            ))}
          </div>
        </section>
      )}
    </section>
  );
}
