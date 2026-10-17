import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, configureApi } from "./api";
import { SessionContext } from "./hooks";
import { AccountPage } from "./pages/AccountPage";
import { AuthPage } from "./pages/AuthPage";
import { BoardPage } from "./pages/BoardPage";
import { CampaignLayout } from "./pages/CampaignLayout";
import { CampaignsPage } from "./pages/CampaignsPage";
import { CharactersPage } from "./pages/CharactersPage";
import { ChatPage } from "./pages/ChatPage";
import { MembersPage } from "./pages/MembersPage";
import type { User } from "./types";

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    configureApi({
      onUnauthorized: () => {
        setUser(null);
        if (!location.pathname.startsWith("/login")) navigate("/login", { replace: true });
      },
    });
  }, [navigate, location.pathname]);

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setChecked(true));
  }, []);

  if (!checked) return <div className="splash" aria-busy="true">DungeonMind</div>;

  return (
    <SessionContext.Provider value={{ user, setUser }}>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <AuthPage />} />
        <Route path="/" element={user ? <CampaignsPage /> : <Navigate to="/login" replace />} />
        <Route path="/account" element={user ? <AccountPage /> : <Navigate to="/login" replace />} />
        <Route path="/c/:cid" element={user ? <CampaignLayout /> : <Navigate to="/login" replace />}>
          <Route index element={<Navigate to="chat" replace />} />
          <Route path="lore" element={<BoardPage board="lore" />} />
          <Route path="characters" element={<CharactersPage />} />
          <Route path="characters/:chid" element={<CharactersPage />} />
          <Route path="journal" element={<BoardPage board="journal" />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="chat/:tid" element={<ChatPage />} />
          <Route path="members" element={<MembersPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </SessionContext.Provider>
  );
}
