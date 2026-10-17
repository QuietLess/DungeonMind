// M9b AU1–AU4: the signed-in user's own account.
import { type FormEvent, useId, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { TopBar } from "../components/TopBar";
import { message, useLoad, useSession } from "../hooks";

/** Runs one request at a time and tracks its error/success text. */
function useAction() {
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const sending = useRef(false);
  const run = async (work: () => Promise<string | null>) => {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      setDone(await work());
    } catch (err) {
      setError(message(err));
    } finally {
      sending.current = false;
      setBusy(false);
    }
  };
  return { error, setError, done, busy, run };
}

function Feedback({ error, done }: { error: string | null; done: string | null }) {
  return (
    <>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {done && (
        <p className="success" role="status">
          {done}
        </p>
      )}
    </>
  );
}

function ChangePassword({ onChanged }: { onChanged: () => void }) {
  const id = useId();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const action = useAction();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (next !== repeat) {
      action.setError("The new passwords don't match.");
      return;
    }
    void action.run(async () => {
      await api.changePassword(current, next);
      onChanged();
      return "Password changed. Your other devices were signed out.";
    });
  };

  return (
    <section className="panel" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`}>Change password</h2>
      <form onSubmit={submit}>
        <label>
          Current password
          <input
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
            maxLength={200}
          />
        </label>
        <label>
          New password
          <input
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            required
            minLength={10}
            maxLength={200}
          />
        </label>
        <label>
          Repeat new password
          <input
            type="password"
            autoComplete="new-password"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
            required
            minLength={10}
            maxLength={200}
          />
        </label>
        <p className="hint">At least 10 characters.</p>
        <Feedback error={action.error} done={action.done} />
        <button className="btn btn-primary" disabled={action.busy}>
          Change password
        </button>
      </form>
    </section>
  );
}

function Devices({ onSignedOut }: { onSignedOut: () => void }) {
  const id = useId();
  const action = useAction();
  const signOut = () =>
    action.run(async () => {
      const { signed_out } = await api.signOutOthers();
      onSignedOut();
      return signed_out === 1 ? "Signed out 1 other device." : `Signed out ${signed_out} other devices.`;
    });
  return (
    <section className="panel" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`}>Devices</h2>
      <p className="muted">Lost a phone or used a shared computer? End every session but this one.</p>
      <Feedback error={action.error} done={action.done} />
      <button type="button" className="btn" disabled={action.busy} onClick={signOut}>
        Sign out other devices
      </button>
    </section>
  );
}

function DeleteAccount({ username }: { username: string }) {
  const id = useId();
  const { setUser } = useSession();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const action = useAction();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void action.run(async () => {
      await api.deleteAccount(password, confirm);
      setUser(null);
      navigate("/login", { replace: true });
      return null;
    });
  };

  return (
    <section className="panel panel-danger" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`}>Delete account</h2>
      <p className="muted">
        Your private entries and your chats are deleted for good. Entries you shared with the DM or
        the party stay in those campaigns. If you're a DM, delete your campaigns first.
      </p>
      <form onSubmit={submit}>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            maxLength={200}
          />
        </label>
        <label>
          Type your username ({username}) to confirm
          <input
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            required
          />
        </label>
        <Feedback error={action.error} done={action.done} />
        <button className="btn btn-danger" disabled={action.busy || confirm !== username || !password}>
          Delete my account
        </button>
      </form>
    </section>
  );
}

export function AccountPage() {
  const { user } = useSession();
  const { data, error, loading, reload } = useLoad(() => api.account(), []);
  const username = data?.username ?? user?.username ?? "";
  const since = data ? new Date(data.created_at).toLocaleDateString() : "";

  return (
    <>
      <TopBar title="Account" />
      <main className="page narrow">
        <h1>Account: {username}</h1>
        {loading && !data && <p className="muted">Loading…</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {data && (
          <dl className="account-facts">
            <dt>Member since</dt>
            <dd>{since}</dd>
            <dt>Questions today</dt>
            <dd>
              {data.questions_today} of {data.daily_limit}
            </dd>
            <dt>Signed-in devices</dt>
            <dd>{data.sessions}</dd>
          </dl>
        )}
        <div className="account-stack">
          <ChangePassword onChanged={reload} />
          <Devices onSignedOut={reload} />
          <DeleteAccount username={username} />
        </div>
      </main>
    </>
  );
}
