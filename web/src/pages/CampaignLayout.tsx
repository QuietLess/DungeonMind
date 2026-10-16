import { NavLink, Outlet, useParams } from "react-router-dom";
import { api } from "../api";
import { TopBar } from "../components/TopBar";
import { CampaignContext, useLoad, useSession } from "../hooks";
import { BookIcon, ChatIcon, CrownIcon, ScrollIcon, UsersIcon } from "../icons";

export function CampaignLayout() {
  const { cid = "" } = useParams();
  const { user } = useSession();
  const { data: campaign, error } = useLoad(() => api.campaign(cid), [cid]);

  if (error) {
    return (
      <>
        <TopBar />
        <main className="page narrow">
          <p className="error" role="alert">{error}</p>
        </main>
      </>
    );
  }
  if (!campaign || !user) return <TopBar />;

  const tabs = [
    { to: "chat", label: "Chat", Icon: ChatIcon },
    { to: "lore", label: "Lore", Icon: ScrollIcon },
    { to: "characters", label: "Characters", Icon: UsersIcon },
    { to: "journal", label: "Journal", Icon: BookIcon },
    { to: "members", label: "Members", Icon: CrownIcon },
  ];

  return (
    <CampaignContext.Provider value={{ campaign, user }}>
      <TopBar title={campaign.name} />
      <div className="campaign-shell">
        <nav className="campaign-nav" aria-label="Campaign sections">
          {tabs.map(({ to, label, Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => (isActive ? "active" : undefined)}>
              <Icon />
              <span>{label}</span>
            </NavLink>
          ))}
          <p className="role-note">
            {campaign.role === "dm" ? "You're the Dungeon Master" : "You're a player"}
          </p>
        </nav>
        <div className="campaign-main">
          <Outlet />
        </div>
      </div>
    </CampaignContext.Provider>
  );
}
