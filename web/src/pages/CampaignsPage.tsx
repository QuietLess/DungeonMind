import { type FormEvent, useState } from "react";
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

  const create = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const campaign = await api.createCampaign(name);
      navigate(`/c/${campaign.id}/lore`);
    } catch (err) {
      setFormError(message(err));
    }
  };

  const join = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const campaign = await api.acceptInvite(code.trim());
      navigate(`/c/${campaign.id}/chat`);
    } catch (err) {
      setFormError(message(err));
    }
  };

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
              <Link to={`/c/${c.id}/${c.role === "dm" ? "lore" : "chat"}`} className="campaign-tile">
                <span className="campaign-name">{c.name}</span>
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
            <button className="btn btn-primary">Create as DM</button>
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
            <button className="btn">Join as player</button>
          </form>
        </div>
      </main>
    </>
  );
}
