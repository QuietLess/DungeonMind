import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useSession } from "../hooks";
import { ShieldIcon } from "../icons";

export function TopBar({ title }: { title?: string }) {
  const { user, setUser } = useSession();
  const navigate = useNavigate();
  const signOut = async () => {
    await api.logout().catch(() => undefined);
    setUser(null);
    navigate("/login");
  };
  return (
    <header className="topbar">
      <Link to="/" className="brand">
        <ShieldIcon />
        <span>DungeonMind</span>
      </Link>
      {title && <span className="topbar-title">{title}</span>}
      <span className="spacer" />
      {user && (
        <>
          <span className="muted user-name">{user.username}</span>
          <button type="button" className="btn btn-small btn-ghost" onClick={signOut}>
            Sign out
          </button>
        </>
      )}
    </header>
  );
}
