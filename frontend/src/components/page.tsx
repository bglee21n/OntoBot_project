// 화면 틀 부품 — 페이지 머리(제목 + ⓘ 도움말 + 오른쪽 주요 단추), 빈 상태, 표 행의 ⋯ 동작 메뉴.
import { type MouseEvent as ReactMouseEvent, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ko } from "../i18n/ko";

const helpKey = (id: string) => `ontobot.help.${id}`;
function readHelp(id: string): boolean {
  try {
    return window.localStorage.getItem(helpKey(id)) === "1";
  } catch {
    return false;
  }
}

/**
 * 제목 줄. 설명(intro)은 ⓘ 를 눌러 펼친다 — 펼침 여부는 화면마다 이 브라우저에 남는다.
 * `actions` 는 그 화면의 주된 일(만들기·게시 등) 단추로, 제목 줄 오른쪽에 둔다.
 */
export function PageHeader({ id, title, intro, actions, badge }: { id: string; title: ReactNode; intro?: ReactNode; actions?: ReactNode; badge?: ReactNode }) {
  const [open, setOpen] = useState(() => readHelp(id));
  const toggle = () => {
    const next = !open;
    setOpen(next);
    try {
      window.localStorage.setItem(helpKey(id), next ? "1" : "0");
    } catch {
      // 저장이 막힌 브라우저 — 이번 화면에서만.
    }
  };
  return (
    <div className="page-header">
      <div className="page-header-row">
        <h1>{title}</h1>
        {badge}
        {intro && (
          <button
            type="button"
            className="link help-toggle"
            aria-expanded={open}
            aria-controls={`help-${id}`}
            title={open ? ko.page.hideHelp : ko.page.showHelp}
            onClick={toggle}
          >
            <span aria-hidden="true">ⓘ</span>
            <span className="sr-only">{open ? ko.page.hideHelp : ko.page.showHelp}</span>
          </button>
        )}
        <span className="spacer" />
        {actions && <div className="page-actions">{actions}</div>}
      </div>
      {intro && open && (
        <p id={`help-${id}`} className="muted page-intro">
          {intro}
        </p>
      )}
    </div>
  );
}

/** 목록이 비었을 때 — 무엇이 없는지와, 할 수 있으면 첫 행동 단추. */
export function EmptyState({ message, action }: { message: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <p>{message}</p>
      {action}
    </div>
  );
}

export type RowAction = { label: string; onSelect: () => void; danger?: boolean; disabled?: boolean };

/**
 * 표 행의 ⋯ 메뉴. 지우기 같은 위험한 일은 맨 아래 줄로 따로 둔다.
 * 표가 가로 스크롤 상자 안에 있어도 잘리지 않게 화면 기준(fixed)으로 띄운다.
 */
export function RowMenu({ items, label }: { items: RowAction[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!open || !btn.current) return;
    const r = btn.current.getBoundingClientRect();
    setPos({ top: r.bottom + 4, right: Math.max(8, window.innerWidth - r.right) });
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!panel.current?.contains(t) && !btn.current?.contains(t)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onScroll = () => setOpen(false);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);
  const safe = items.filter((i) => !i.danger);
  const danger = items.filter((i) => i.danger);
  const item = (i: RowAction) => (
    <button
      key={i.label}
      type="button"
      role="menuitem"
      className={i.danger ? "menu-item danger" : "menu-item"}
      disabled={i.disabled}
      onClick={() => {
        setOpen(false);
        i.onSelect();
      }}
    >
      {i.label}
    </button>
  );
  return (
    <>
      <button
        ref={btn}
        type="button"
        className="row-menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label ?? ko.page.moreActions}
        title={label ?? ko.page.moreActions}
        onClick={() => setOpen((o) => !o)}
      >
        ⋯
      </button>
      {open && pos && (
        <div ref={panel} className="row-menu card" role="menu" style={{ top: pos.top, right: pos.right }}>
          {safe.map(item)}
          {safe.length > 0 && danger.length > 0 && <hr />}
          {danger.map(item)}
        </div>
      )}
    </>
  );
}

const INTERACTIVE = "a, button, input, select, textarea, label, summary, [role='menu'], [role='menuitem']";

/**
 * 표 행 전체를 눌러 상세로 연다(`<tr className="row-link" onClick={rowLink(to)}>`). 행 안의 링크·단추·⋯·체크박스를
 * 누를 때와 글자를 끌어 고를 때는 열지 않는다. 키보드로는 행 안의 "열기" 링크를 쓴다(그 링크는 남겨 둔다).
 */
export function useRowLink() {
  const navigate = useNavigate();
  return (to: string) => (e: ReactMouseEvent<HTMLTableRowElement>) => {
    if ((e.target as HTMLElement).closest(INTERACTIVE)) return;
    if (window.getSelection()?.toString()) return;
    navigate(to);
  };
}
