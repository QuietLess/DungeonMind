import { type FormEvent, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { TopBar } from "../components/TopBar";
import { message, useLoad } from "../hooks";
import { CrownIcon, UsersIcon } from "../icons";

export function CampaignsPage() {
  const navigate = useNavigate();
  const { data: campaigns, error, loading } = useLoad(() => api.campaigns(), []);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);

  /** One request at a time (F2): a double click doesn't create two campaigns. */
  const once = async (e: FormEvent, run: () => Promise<string>) => {
    e.preventDefault();
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setFormError(null);
    try {
      navigate(await run());
    } catch (err) {
      setFormError(message(err));
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };

  const create = (e: FormEvent) =>
    once(e, async () => `/c/${(await api.createCampaign(name)).id}/overview`);
  const join = (e: FormEvent) =>
    once(e, async () => `/c/${(await api.acceptInvite(code.trim())).id}/overview`);

  return (
    <>
      <TopBar />
      <main className="page narrow">
        <h1>Your campaigns</h1>
        {loading && <p className="muted">Loading…</p>}
        {error && <p className="error" role="alert">{error}</p>}
        {campaigns && campaigns.length === 0 && (
          <p className="muted">No campaigns yet. Start one, or join with an invite code from your DM.</p>
        )}
        <ul className="campaign-list">
          {campaigns?.map((c) => (
            <li key={c.id}>
              <Link to={`/c/${c.id}/overview`} className="campaign-tile">
                <span className="monogram" aria-hidden="true">
                  {c.name.trim().charAt(0).toLocaleUpperCase("tr")}
                </span>
                <span className="campaign-text">
                  <span className="campaign-name">{c.name}</span>
                  {c.created_at && (
                    <span className="campaign-since">
                      Started{" "}
                      {new Date(c.created_at).toLocaleDateString(undefined, {
                        month: "long",
                        year: "numeric",
                      })}
                    </span>
                  )}
                </span>
                <span className="role-tag">
                  {c.role === "dm" ? <CrownIcon /> : <UsersIcon />}
                  {c.role === "dm" ? "Dungeon Master" : "Player"}
                </span>
              </Link>
            </li>
          ))}
        </ul>

        {formError && <p className="error" role="alert">{formError}</p>}
        <div className="two-col">
          <form className="panel" onSubmit={create}>
            <h2>Start a campaign</h2>
            <label>
              Campaign name
              <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} />
            </label>
            <button className="btn btn-primary" disabled={busy}>Create as DM</button>
          </form>
          <form className="panel" onSubmit={join}>
            <h2>Join a campaign</h2>
            <label>
              Invite code
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                required
                autoComplete="off"
                spellCheck={false}
                placeholder="e.g. kq7h3m9xw2ab"
              />
            </label>
            <button className="btn" disabled={busy}>Join as player</button>
          </form>
        </div>
      </main>
    </>
  );
}
