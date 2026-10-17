// M10 R3: the campaign's game log. Polls for newer rolls every 3 s while mounted; the server
// decides which rolls this viewer may see. With `roller`, also rolls free expressions.
import { type FormEvent, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { message } from "../hooks";
import type { Roll } from "../types";

const POLL_MS = 3000;
const KEEP = 60;

export function rollDetail(roll: Roll): string {
  const dice = roll.terms
    .map((t) => (t.rolls.length === t.kept.length ? `[${t.rolls.join(", ")}]` : `[${t.rolls.join(", ")} → ${t.kept.join(", ")}]`))
    .join(" ");
  const bonus = roll.bonus ? ` ${roll.bonus > 0 ? "+" : "−"} ${Math.abs(roll.bonus)}` : "";
  return `${roll.expression}: ${dice}${bonus}`;
}

export function GameLog({
  cid,
  roller = false,
  incoming,
}: {
  cid: string;
  roller?: boolean;
  /** A roll this page just made, shown at once instead of waiting for the next poll. */
  incoming?: Roll | null;
}) {
  const [rolls, setRolls] = useState<Roll[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expression, setExpression] = useState("");
  const [toDm, setToDm] = useState(false);
  const last = useRef(0);

  const add = (more: Roll[]) => {
    const fresh = more.filter((r) => r.id > last.current);
    if (!fresh.length) return;
    last.current = Math.max(last.current, ...fresh.map((r) => r.id));
    setRolls((old) => [...fresh.reverse(), ...old].slice(0, KEEP));
  };

  useEffect(() => {
    let live = true;
    const poll = () => {
      if (typeof document !== "undefined" && document.hidden) return;
      api
        .rolls(cid, last.current)
        .then((more) => live && add(more))
        .catch(() => undefined); // a missed poll is retried on the next tick
    };
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [cid]);

  useEffect(() => {
    if (incoming) add([incoming]);
  }, [incoming]);

  const roll = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      add([await api.roll(cid, { kind: "expression", expression: expression.trim(), mode: "normal", to_dm: toDm })]);
    } catch (err) {
      setError(message(err));
    }
  };

  return (
    <section className="game-log" aria-labelledby="game-log-h">
      <h2 id="game-log-h">Game log</h2>
      {roller && (
        <form className="free-roll" onSubmit={roll}>
          <label>
            <span className="sr-only">Dice to roll</span>
            <input
              aria-label="Dice to roll"
              value={expression}
              onChange={(e) => setExpression(e.target.value)}
              placeholder="e.g. 2d6+3"
              maxLength={64}
              required
            />
          </label>
          <button className="btn btn-primary">Roll</button>
          <label className="check">
            <input type="checkbox" checked={toDm} onChange={(e) => setToDm(e.target.checked)} /> to DM only
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
      {rolls.length === 0 && <p className="muted">No rolls yet.</p>}
      <ol className="log-list" aria-live="polite">
        {rolls.map((r) => (
          <li key={r.id} className={r.natural === 20 ? "nat20" : r.natural === 1 ? "nat1" : undefined}>
            <div className="log-head">
              <span className="log-label">{r.label}</span>
              <span className="log-total">{r.total}</span>
            </div>
            <div className="log-detail">
              {rollDetail(r)}
              {r.mode !== "normal" && <span className="log-tag">{r.mode}</span>}
              {r.outcome && <span className="log-tag">{r.outcome}</span>}
              {r.to_dm && <span className="log-tag dm">DM only</span>}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
