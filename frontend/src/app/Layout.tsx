import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { ThemeToggle } from "../components/ThemeToggle";

import { useNotifications } from "../features/actions/ActionsPage";
import { useEngagements } from "../features/adoption/AdoptionPage";
import { ko } from "../i18n/ko";
import { useLogout, useMe } from "./auth";

export function Layout() {
  const me = useMe();
  const logout = useLogout();
  const notes = useNotifications();
  const unread = notes.data?.unread ?? 0;
  // 도입 메뉴: 만들 수 있는 역할(modeler·admin)이거나 어느 도입의 구성원일 때(TRD 17.17).
  const canCreateEngagement = me.data?.roles.some((r) => r === "modeler" || r === "admin") ?? false;
  const engagements = useEngagements(Boolean(me.data) && !canCreateEngagement);
  const showAdoption = canCreateEngagement || (engagements.data?.length ?? 0) > 0;
  // 좁은 화면(휴대폰)에서는 메뉴를 접는다. 메뉴 안을 누르면(링크 이동) 다시 접는다.
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="shell">
      <header className="topbar">
        <strong>{ko.appTitle}</strong>
        <button
          type="button"
          className="link menu-toggle"
          aria-expanded={menuOpen}
          aria-controls="main-nav"
          onClick={() => setMenuOpen((o) => !o)}
        >
          <span aria-hidden="true">☰</span> {ko.nav.menu}
        </button>
        <nav id="main-nav" className={menuOpen ? "collapsible open" : "collapsible"} onClick={() => setMenuOpen(false)}>
          <NavLink to="/chat">{ko.nav.chat}</NavLink>
          {me.data?.roles.some((r) => r === "domain_expert" || r === "modeler") && (
            <NavLink to="/workshop">{ko.nav.workshop}</NavLink>
          )}
          {showAdoption && <NavLink to="/adoption">{ko.nav.adoption}</NavLink>}
          <NavLink to="/explore">{ko.nav.explore}</NavLink>
          <NavLink to="/documents">{ko.nav.documents}</NavLink>
          <NavLink to="/ontology">{ko.nav.ontology}</NavLink>
          {me.data?.roles.some((r) => r === "bot_builder" || r === "modeler") && (
            <NavLink to="/eval">{ko.nav.eval}</NavLink>
          )}
          <NavLink to="/sources">{ko.nav.sources}</NavLink>
          <NavLink to="/actions">
            {ko.nav.actions}
            {unread > 0 && <span className="badge failed"> {unread}</span>}
          </NavLink>
          {me.data?.roles.includes("admin") && <NavLink to="/traces">{ko.nav.traces}</NavLink>}
          {me.data?.roles.includes("admin") && <NavLink to="/admin">{ko.nav.admin}</NavLink>}
        </nav>
        <span className="spacer" />
        {me.data && (
          <span className="muted user-name">
            {me.data.display_name}
            <span className="hide-narrow"> · {me.data.roles.join(", ")}</span>
          </span>
        )}
        <ThemeToggle />
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
