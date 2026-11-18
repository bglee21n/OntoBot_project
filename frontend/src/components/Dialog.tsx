// 대화상자 — 가끔 하는 만들기(새 인터뷰·새 도입·문서 올리기·키 발급 등)를 목록 위에 늘 펼쳐 두지 않고 여기서 연다.
// Esc·바깥 누르기로 닫고, 초점은 창 안에서만 돈다(aria-modal). 닫으면 연 단추로 초점을 돌려준다.
import { type ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { ko } from "../i18n/ko";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Dialog({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  /** 칸이 많은 폼(골든셋·Action 정의 등) — 넓게 */
  wide?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const close = useRef(onClose);
  useLayoutEffect(() => {
    close.current = onClose;
  });
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const first = box.current?.querySelector<HTMLElement>(
      "input:not([type=hidden]):not([disabled]), select, textarea",
    );
    (first ?? box.current)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close.current();
        return;
      }
      if (e.key !== "Tab" || !box.current) return;
      const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const head = items[0]!;
      const tail = items[items.length - 1]!;
      const active = document.activeElement;
      if (e.shiftKey && (active === head || !box.current.contains(active))) {
        e.preventDefault();
        tail.focus();
      } else if (!e.shiftKey && (active === tail || !box.current.contains(active))) {
        e.preventDefault();
        head.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    document.body.classList.add("dialog-open");
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.classList.remove("dialog-open");
      opener?.focus?.();
    };
  }, []);
  return createPortal(
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={box}
        className={wide ? "dialog dialog-wide card stack" : "dialog card stack"}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="dialog-head">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="link dialog-close" aria-label={ko.common.close} title={ko.common.close} onClick={onClose}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}

/**
 * 머리의 "+ 새로 만들기" 단추와 대화상자를 함께 — `children` 에 닫기 함수를 넘긴다.
 * 결과를 보여 줘야 하는 흐름(키 원문 한 번 표시 등)은 children 이 창 안에서 결과로 바꿔 그리면 된다.
 */
export function CreateDialogButton({
  label,
  title,
  children,
  wide,
  className,
}: {
  label: string;
  title?: ReactNode;
  children: (close: () => void) => ReactNode;
  wide?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>
        {label}
      </button>
      {open && (
        <Dialog title={title ?? label.replace(/^\+\s*/, "")} wide={wide} onClose={() => setOpen(false)}>
          {children(() => setOpen(false))}
        </Dialog>
      )}
    </>
  );
}

/** 대화상자 아래 단추 줄 — 취소(보조)와 주된 일(제출). */
export function DialogActions({
  onCancel,
  pending,
  submitLabel,
  disabled,
}: {
  onCancel: () => void;
  pending?: boolean;
  submitLabel: string;
  disabled?: boolean;
}) {
  return (
    <div className="row dialog-actions">
      <span className="spacer" />
      <button type="button" className="secondary" onClick={onCancel}>
        {ko.common.cancel}
      </button>
      <button type="submit" disabled={pending || disabled}>
        {submitLabel}
      </button>
    </div>
  );
}
