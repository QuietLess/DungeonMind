import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { message, useCampaign, useLoad } from "../hooks";
import { CrownIcon, PlusIcon, UsersIcon } from "../icons";
import type { Invite } from "../types";

export function MembersPage() {
  const { campaign, user } = useCampaign();
  const navigate = useNavigate();
  const { data: members, reload } = useLoad(() => api.members(campaign.id), [campaign.id]);
  const [invite, setInvite] = useState<Invite | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isDm = campaign.role === "dm";

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(message(e));
    }
  };

  const copy = async () => {
    if (!invite) return;
    await navigator.clipboard?.writeText(invite.code).catch(() => undefined);
    setCopied(true);
  };

  return (
    <section className="board narrow-board">
      <header className="board-head">
        <div>
          <h1>Members</h1>
          <p className="muted">
            {isDm
              ? "Invite players with a one-time code. Each code works once and expires after 7 days."
              : "The people in this campaign."}
          </p>
        </div>
        {isDm && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => run(async () => {
              setInvite(await api.createInvite(campaign.id));
              setCopied(false);
            })}
          >
            <PlusIcon /> New invite code
          </button>
        )}
      </header>
      {invite && (
        <div className="invite panel">
          <span className="field-label">Invite code</span>
          <code className="invite-code">{invite.code}</code>
          <button type="button" className="btn btn-small" onClick={copy}>
            {copied ? "Copied" : "Copy"}
          </button>
          <p className="hint">
            Expires {new Date(invite.expires_at).toLocaleDateString()}. Players enter it under
            “Join a campaign”.
          </p>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <ul className="member-list">
        {members?.map((m) => (
          <li key={m.user_id}>
            <span className="role-tag">{m.role === "dm" ? <CrownIcon /> : <UsersIcon />}</span>
            <span className="member-name">
              {m.username}
              {m.user_id === user.id && <span className="muted"> (you)</span>}
            </span>
            <span className="muted">{m.role === "dm" ? "Dungeon Master" : "Player"}</span>
            {isDm && m.role === "player" && (
              <button
                type="button"
                className="btn btn-small btn-ghost danger"
                onClick={() =>
                  window.confirm(`Remove ${m.username}? They lose access at once; their entries stay.`) &&
                  run(async () => {
                    await api.removeMember(campaign.id, m.user_id);
                    reload();
                  })
                }
              >
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>
      {!isDm && (
        <button
          type="button"
          className="btn btn-ghost danger"
          onClick={() =>
            window.confirm("Leave this campaign? You'll need a new invite to come back.") &&
            run(async () => {
              await api.removeMember(campaign.id, user.id);
              navigate("/");
            })
          }
        >
          Leave campaign
        </button>
      )}
      {isDm && (
        <button
          type="button"
          className="btn btn-ghost danger"
          onClick={() =>
            window.prompt(`Type the campaign name to delete it for everyone: ${campaign.name}`) ===
              campaign.name &&
            run(async () => {
              await api.deleteCampaign(campaign.id);
              navigate("/");
            })
          }
        >
          Delete campaign
        </button>
      )}
    </section>
  );
}
