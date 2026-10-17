// L4: the campaign's home. Everything comes from visibility-bound API calls, so the counts
// and lists show only what this viewer may read.
import { type ReactElement, useId, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { EntryPanel } from "../components/EntryPanel";
import { kindLabel } from "../components/EntryCard";
import { useCampaign, useLoad } from "../hooks";
import { BookIcon, ChatIcon, PlusIcon, ScrollIcon, UsersIcon } from "../icons";
import type { Entry } from "../types";

function Stat({
  label,
  value,
  detail,
  to,
  Icon,
}: {
  label: string;
  value: number | undefined;
  detail?: string;
  to: string;
  Icon: () => ReactElement;
}) {
  const id = useId();
  return (
    <section className="stat" aria-labelledby={id}>
      <Link to={to} className="stat-link">
        <span className="stat-icon">
          <Icon />
        </span>
        <span id={id} className="stat-label">
          {label}
        </span>
        <span className="stat-value">{value ?? "–"}</span>
        {detail && <span className="stat-detail">{detail}</span>}
      </Link>
    </section>
  );
}

const byUpdated = (a: Entry, b: Entry) => b.updated_at.localeCompare(a.updated_at);

export function OverviewPage() {
  const { campaign } = useCampaign();
  const base = `/c/${campaign.id}`;
  const isDm = campaign.role === "dm";
  const { data: entries } = useLoad(() => api.entries(campaign.id), [campaign.id]);
  const { data: characters } = useLoad(() => api.characters(campaign.id), [campaign.id]);
  const { data: members } = useLoad(() => api.members(campaign.id), [campaign.id]);
  const [open, setOpen] = useState<number | null>(null);

  const lore = entries?.filter((e) => e.board === "lore") ?? [];
  const journal = entries?.filter((e) => e.board === "journal") ?? [];
  const secret = lore.filter((e) => e.visibility !== "party").length;
  const sessions = [...journal]
    .sort((a, b) => (b.session ?? -1) - (a.session ?? -1) || byUpdated(a, b))
    .slice(0, 3);
  const recent = [...(entries ?? [])].sort(byUpdated).slice(0, 6);

  return (
    <section className="overview">
      <header className="overview-head">
        <div>
          <p className="eyebrow">{isDm ? "You're the Dungeon Master" : "You're a player"}</p>
          <h1>{campaign.name}</h1>
        </div>
        <div className="overview-actions">
          <Link to={`${base}/chat`} className="btn btn-primary">
            <ChatIcon /> Ask the chatbot
          </Link>
          {isDm && (
            <Link to={`${base}/lore`} className="btn">
              <PlusIcon /> New lore entry
            </Link>
          )}
          <Link to={`${base}/journal`} className="btn">
            <PlusIcon /> New journal entry
          </Link>
        </div>
      </header>

      <div className="stats">
        <Stat
          label="Lore"
          value={entries ? lore.length : undefined}
          detail={isDm && entries ? `${secret} secret · ${lore.length - secret} revealed` : undefined}
          to={`${base}/lore`}
          Icon={ScrollIcon}
        />
        <Stat label="Characters" value={characters?.length} to={`${base}/characters`} Icon={UsersIcon} />
        <Stat label="Journal" value={entries ? journal.length : undefined} to={`${base}/journal`} Icon={BookIcon} />
        <Stat label="Members" value={members?.length} to={`${base}/members`} Icon={UsersIcon} />
      </div>

      <div className="overview-cols">
        <section className="panel" aria-labelledby="ov-sessions">
          <h2 id="ov-sessions">Latest sessions</h2>
          {entries && sessions.length === 0 && <p className="muted">No session notes yet.</p>}
          <ul className="overview-list" aria-label="Latest sessions">
            {sessions.map((e) => (
              <li key={e.id}>
                <button type="button" className="overview-item" onClick={() => setOpen(e.id)}>
                  {e.session != null && <span className="session-badge">{e.session}</span>}
                  <span>{e.title}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
        <section className="panel" aria-labelledby="ov-recent">
          <h2 id="ov-recent">Recently changed</h2>
          {entries && recent.length === 0 && <p className="muted">Nothing written yet.</p>}
          <ul className="overview-list" aria-label="Recently changed">
            {recent.map((e) => (
              <li key={e.id}>
                <button type="button" className="overview-item" onClick={() => setOpen(e.id)}>
                  <span className={`kind kind-${e.board === "journal" ? "journal" : e.kind}`}>
                    {e.board === "journal" ? "Journal" : kindLabel(e.kind)}
                  </span>
                  <span>{e.title}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>
      {open !== null && <EntryPanel entryId={open} onClose={() => setOpen(null)} />}
    </section>
  );
}
