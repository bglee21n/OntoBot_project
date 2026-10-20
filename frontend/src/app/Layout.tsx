import { NavLink, Outlet } from "react-router-dom";

import { useNotifications } from "../features/actions/ActionsPage";
import { ko } from "../i18n/ko";
import { useLogout, useMe } from "./auth";

export function Layout() {
  const me = useMe();
  const logout = useLogout();
  const notes = useNotifications();
  const unread = notes.data?.unread ?? 0;
  return (
    <div className="shell">
      <header className="topbar">
        <strong>{ko.appTitle}</strong>
        <nav>
          <NavLink to="/chat">{ko.nav.chat}</NavLink>
          {me.data?.roles.some((r) => r === "domain_expert" || r === "modeler") && (
            <NavLink to="/workshop">{ko.nav.workshop}</NavLink>
          )}
          <NavLink to="/ontology">{ko.nav.ontology}</NavLink>
          {me.data?.roles.some((r) => r === "bot_builder" || r === "modeler") && (
            <NavLink to="/eval">{ko.nav.eval}</NavLink>
          )}
          <NavLink to="/sources">{ko.nav.sources}</NavLink>
          <NavLink to="/actions">
            {ko.nav.actions}
            {unread > 0 && <span className="badge failed"> {unread}</span>}
          </NavLink>
          {me.data?.roles.includes("admin") && <NavLink to="/admin">{ko.nav.admin}</NavLink>}
        </nav>
        <span className="spacer" />
        {me.data && (
          <span className="muted">
            {me.data.display_name} · {me.data.roles.join(", ")}
          </span>
        )}
        <button type="button" className="link" onClick={() => logout.mutate()}>
          {ko.nav.logout}
        </button>
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
