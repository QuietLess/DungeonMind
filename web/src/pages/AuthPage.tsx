import { type FormEvent, useState } from "react";
import { api } from "../api";
import { message, useSession } from "../hooks";
import { ShieldIcon } from "../icons";

export function AuthPage() {
  const { setUser } = useSession();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user =
        mode === "login"
          ? await api.login(username, password)
          : await api.register(username, password);
      setUser(user);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth">
      <div className="auth-card">
        <div className="brand">
          <ShieldIcon />
          <span>DungeonMind</span>
        </div>
        <p className="tagline">Your campaign's lore, journals and a rules-wise companion.</p>
        <h1>{mode === "login" ? "Sign in" : "Create an account"}</h1>
        <form onSubmit={submit}>
          <label>
            Username
            <input
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value.toLowerCase())}
              required
              minLength={3}
              maxLength={32}
              pattern="[a-z0-9_]{3,32}"
              aria-describedby={mode === "register" ? "username-hint" : undefined}
            />
          </label>
          {mode === "register" && (
            <p id="username-hint" className="hint">
              3–32 characters: lowercase letters, digits and _
            </p>
          )}
          <label>
            Password
            <input
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={mode === "register" ? 10 : 1}
              maxLength={200}
            />
          </label>
          {mode === "register" && <p className="hint">At least 10 characters.</p>}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
        <button
          type="button"
          className="link-button"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
        >
          {mode === "login" ? "New here? Create an account" : "Already have an account? Sign in"}
        </button>
      </div>
    </main>
  );
}
