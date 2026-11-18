import { type ReactNode, useEffect, useRef, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { ThemeToggle } from "../components/ThemeToggle";
import { useNotifications } from "../features/actions/ActionsPage";
import { useEngagements } from "../features/adoption/AdoptionPage";
import { ko } from "../i18n/ko";
import { useLogout, useMe } from "./auth";

/** 메뉴는 쓰임새별 세 묶음이다. 넓은 화면은 왼쪽에 늘 펼쳐 두고, 좁은 화면은 ☰ 로 여는 서랍이 된다. */
export function Layout() {
  const me = useMe();
  const notes = useNotifications();
  const unread = notes.data?.unread ?? 0;
  const roles = me.data?.roles ?? [];
  const has = (...r: string[]) => roles.some((x) => r.includes(x));
  // 도입 메뉴: 만들 수 있는 역할(modeler·admin)이거나 어느 도입의 구성원일 때(TRD 17.17).
  const canCreateEngagement = has("modeler", "admin");
  const engagements = useEngagements(Boolean(me.data) && !canCreateEngagement);
  const showAdoption = canCreateEngagement || (engagements.data?.length ?? 0) > 0;
  // 좁은 화면에서 서랍을 연다. 메뉴 안을 누르면(링크 이동) 닫는다.
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const groups: { title: string; items: ReactNode[] }[] = [
    {
      title: ko.nav.groups.work,
      items: [
        <NavLink key="chat" to="/chat">{ko.nav.chat}</NavLink>,
        <NavLink key="explore" to="/explore">{ko.nav.explore}</NavLink>,
        <NavLink key="documents" to="/documents">{ko.nav.documents}</NavLink>,
        <NavLink key="actions" to="/actions">
          {ko.nav.actions}
          {unread > 0 && <span className="badge failed nav-count">{unread}</span>}
        </NavLink>,
      ],
    },
    {
      title: ko.nav.groups.build,
      items: [
        has("domain_expert", "modeler") && <NavLink key="workshop" to="/workshop">{ko.nav.workshop}</NavLink>,
        <NavLink key="ontology" to="/ontology">{ko.nav.ontology}</NavLink>,
        <NavLink key="sources" to="/sources">{ko.nav.sources}</NavLink>,
        has("bot_builder", "modeler") && <NavLink key="eval" to="/eval">{ko.nav.eval}</NavLink>,
      ].filter(Boolean),
    },
    {
      title: ko.nav.groups.operate,
      items: [
        showAdoption && <NavLink key="adoption" to="/adoption">{ko.nav.adoption}</NavLink>,
        has("admin") && <NavLink key="traces" to="/traces">{ko.nav.traces}</NavLink>,
        has("admin") && <NavLink key="admin" to="/admin">{ko.nav.admin}</NavLink>,
      ].filter(Boolean),
    },
  ].filter((g) => g.items.length > 0);

  return (
    <div className="shell">
      <header className="topbar app-topbar">
        <button
          type="button"
          className="link menu-toggle"
          aria-expanded={menuOpen}
          aria-controls="main-nav"
          onClick={() => setMenuOpen((o) => !o)}
        >
          <span aria-hidden="true">☰</span> {ko.nav.menu}
        </button>
        <strong className="brand">{ko.appTitle}</strong>
        <span className="spacer" />
        <UserMenu />
      </header>
      <div className="shell-body">
        {menuOpen && <div className="nav-backdrop" onClick={() => setMenuOpen(false)} aria-hidden="true" />}
        <nav id="main-nav" className={menuOpen ? "sidenav open" : "sidenav"} aria-label={ko.nav.menu} onClick={() => setMenuOpen(false)}>
          {groups.map((g) => (
            <div key={g.title} className="nav-group">
              <div className="nav-group-title">{g.title}</div>
              {g.items}
            </div>
          ))}
        </nav>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/** 이름을 누르면 역할·테마·로그아웃이 나온다. 바깥을 누르거나 Esc 로 닫는다. */
function UserMenu() {
  const me = useMe();
  const logout = useLogout();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  if (!me.data) return null;
  const roleName = (r: string) => (ko.roles as Record<string, string>)[r] ?? r;
  return (
    <div className="user-menu" ref={box}>
      <button type="button" className="link user-menu-button" aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen((o) => !o)}>
        <span className="user-name">{me.data.display_name}</span> <span aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="user-menu-panel card" role="menu">
          <div className="stack-tight">
            <strong>{me.data.display_name}</strong>
            <div className="row-tight">
              {me.data.roles.map((r) => (
                <span key={r} className="badge" title={r}>
                  {roleName(r)}
                </span>
              ))}
            </div>
          </div>
          <hr />
          <ThemeToggle />
          <button type="button" className="link" role="menuitem" onClick={() => logout.mutate()}>
            {ko.nav.logout}
          </button>
        </div>
      )}
    </div>
  );
}
