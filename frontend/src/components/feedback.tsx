// 확인 창과 토스트 알림 — 앱 어디서든 함수로 부르고, 그리는 자리는 루트의 <FeedbackHost /> 하나다.
// window.confirm 대신 쓴다(브라우저 기본 창은 모양·문구를 맞출 수 없고 다크 모드도 따르지 않는다).
import { useEffect, useRef, useSyncExternalStore } from "react";

import { ko } from "../i18n/ko";

type Toast = { id: number; message: string; kind: "ok" | "error" };
type Confirm = {
  message: string;
  title?: string;
  confirmLabel?: string;
  danger?: boolean;
  resolve: (ok: boolean) => void;
};

let toasts: Toast[] = [];
let pending: Confirm | null = null;
let seq = 0;
const listeners = new Set<() => void>();
// useSyncExternalStore 는 바뀌지 않았으면 같은 객체를 받아야 한다 — 바뀔 때만 새로 만든다.
let snapshot: { toasts: Toast[]; pending: Confirm | null } = { toasts, pending };
const EMPTY = { toasts: [] as Toast[], pending: null as Confirm | null };
const emit = () => {
  snapshot = { toasts, pending };
  listeners.forEach((l) => l());
};
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

/** 화면 아래에 잠깐 뜨는 알림(저장·삭제·실행 결과). 오류는 그 자리의 ErrorBox 가 맡는다. */
export function toast(message: string, kind: Toast["kind"] = "ok"): void {
  const id = ++seq;
  toasts = [...toasts, { id, message, kind }].slice(-3);
  emit();
  window.setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== id);
    emit();
  }, 3500);
}

/** 확인 창을 띄우고 고른 결과를 돌려준다. 이미 떠 있으면 앞의 것을 취소로 닫는다. */
export function confirmDialog(
  message: string,
  opts: { title?: string; confirmLabel?: string; danger?: boolean } = {},
): Promise<boolean> {
  pending?.resolve(false);
  return new Promise((resolve) => {
    pending = { message, ...opts, resolve };
    emit();
  });
}

function close(ok: boolean) {
  const p = pending;
  pending = null;
  emit();
  p?.resolve(ok);
}

export function FeedbackHost() {
  const state = useSyncExternalStore(subscribe, () => snapshot, () => EMPTY);
  return (
    <>
      {state.pending && <ConfirmView c={state.pending} />}
      <div className="toasts" role="status" aria-live="polite">
        {state.toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.message}
          </div>
        ))}
      </div>
    </>
  );
}

function ConfirmView({ c }: { c: Confirm }) {
  const t = ko.common;
  const cancelRef = useRef<HTMLButtonElement>(null);
  const okRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    // 위험한 일은 취소에, 그 밖은 확인에 초점을 둔다(Enter 를 잘못 눌러도 지워지지 않게).
    (c.danger ? cancelRef : okRef).current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(false);
      if (e.key === "Tab") {
        // 초점이 창 밖으로 나가지 않게 두 단추 사이에서만 돈다.
        e.preventDefault();
        (document.activeElement === okRef.current ? cancelRef : okRef).current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [c]);
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && close(false)}>
      <div className="dialog card stack" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-message">
        <strong id="confirm-title">{c.title ?? t.confirmTitle}</strong>
        <p id="confirm-message" className="confirm-message">
          {c.message}
        </p>
        <div className="row dialog-actions">
          <span className="spacer" />
          <button type="button" className="secondary" ref={cancelRef} onClick={() => close(false)}>
            {t.cancel}
          </button>
          <button type="button" className={c.danger ? "danger-fill" : ""} ref={okRef} onClick={() => close(true)}>
            {c.confirmLabel ?? t.confirm}
          </button>
        </div>
      </div>
    </div>
  );
}
