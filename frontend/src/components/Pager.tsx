// 목록 표 페이지 나누기 — 받아 온 행을 화면에서 나눠 보여 준다(서버 조회 한도는 각 API 가 정한다).
// 페이지당 행 수는 보는 사람이 고르고 이 브라우저에 남는다(모든 목록 표가 같은 값을 쓴다).
import { useState, useSyncExternalStore } from "react";

import { ko } from "../i18n/ko";

export const PAGE_SIZES = [10, 20, 50, 100] as const;
const DEFAULT_SIZE = 20;
const KEY = "ontobot.pageSize";
const listeners = new Set<() => void>();

function read(): number {
  try {
    const n = Number(window.localStorage.getItem(KEY));
    return (PAGE_SIZES as readonly number[]).includes(n) ? n : DEFAULT_SIZE;
  } catch {
    return DEFAULT_SIZE;
  }
}

let current = typeof window === "undefined" ? DEFAULT_SIZE : read();

export function setPageSize(n: number): void {
  current = n;
  try {
    window.localStorage.setItem(KEY, String(n));
  } catch {
    // 저장이 막힌 브라우저 — 이번 화면에서만 바뀐다.
  }
  listeners.forEach((l) => l());
}

export function usePageSize(): number {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
    () => DEFAULT_SIZE,
  );
}

export type PagerState = { page: number; pages: number; total: number; size: number; setPage: (p: number) => void };

/** 행 목록을 지금 페이지 몫으로 자른다. 목록이 줄어 페이지가 넘치면 마지막 페이지를 보인다.
 *  `resetKey`(거르기 조건 등)가 바뀌면 첫 페이지로 돌아간다. */
export function usePaged<T>(items: readonly T[] | null | undefined, resetKey: string = ""): { rows: T[]; pager: PagerState } {
  const size = usePageSize();
  const [state, setState] = useState({ key: resetKey, page: 0 });
  const page = state.key === resetKey ? state.page : 0;
  const setPage = (p: number) => setState({ key: resetKey, page: Math.max(0, p) });
  const all = items ?? [];
  const pages = Math.max(1, Math.ceil(all.length / size));
  const cur = Math.min(page, pages - 1);
  return {
    rows: all.slice(cur * size, cur * size + size),
    pager: { page: cur, pages, total: all.length, size, setPage },
  };
}

/** 한 번에 보이는 페이지 번호 수(10개씩 묶는다: 1~10, 11~20 …). */
const BLOCK = 10;

/** 서버에 남은 행을 이어 받는 표(감사 로그·트레이스 — 커서 방식 조회)가 넘긴다. */
export type MoreRows = { has: boolean; loading: boolean; load: () => void };

/** 표 아래에 둔다 — `<< < 1 2 3 … 10 > >>`. 행이 가장 작은 페이지 크기 이하이면 그리지 않는다.
 *  `more` 가 있으면 불러온 마지막 페이지에서 `>`·`>>` 가 서버의 다음 묶음을 받아 그 페이지로 간다
 *  (따로 "더 보기" 단추를 두지 않는다). 그때 전체 건수는 "N건+" 로 보인다. */
export function Pager({ pager, more }: { pager: PagerState; more?: MoreRows }) {
  const t = ko.pager;
  const { page, pages, total, size, setPage } = pager;
  if (total <= PAGE_SIZES[0] && !more?.has) return null;
  const atEnd = page >= pages - 1;
  const canMore = !!more?.has && !more.loading;
  // 받은 행이 끝나면 다음 묶음을 받고, 요청한 페이지는 행이 도착하면 열린다(usePaged 가 그때까지 마지막 페이지로 둔다).
  // `>>` 는 받은 묶음의 마지막 페이지로(큰 값을 두면 usePaged 가 마지막 페이지로 자른다).
  const forward = (to: number, toEnd = false) => {
    if (!atEnd) {
      setPage(to);
    } else if (canMore) {
      more.load();
      setPage(toEnd ? Number.MAX_SAFE_INTEGER : page + 1);
    }
  };
  const blockStart = Math.floor(page / BLOCK) * BLOCK;
  const numbers = Array.from({ length: Math.min(BLOCK, pages - blockStart) }, (_, i) => blockStart + i);
  const stepTo = (label: string, glyph: string, go: () => void, disabled: boolean) => (
    <button type="button" className="pager-step" onClick={go} disabled={disabled} aria-label={label} title={label}>
      {glyph}
    </button>
  );
  const step = (label: string, glyph: string, to: number, disabled: boolean) => stepTo(label, glyph, () => setPage(to), disabled);
  return (
    <nav className="pager" aria-label={t.label}>
      <div className="pager-pages">
        {step(t.first, "<<", 0, page === 0)}
        {step(t.prev, "<", page - 1, page === 0)}
        {numbers.map((n) => (
          <button
            key={n}
            type="button"
            className={n === page ? "pager-num current" : "pager-num"}
            onClick={() => setPage(n)}
            aria-current={n === page ? "page" : undefined}
            aria-label={t.goTo(n + 1)}
          >
            {n + 1}
          </button>
        ))}
        {stepTo(t.next, ">", () => forward(page + 1), atEnd && !canMore)}
        {stepTo(t.last, ">>", () => forward(pages - 1, true), atEnd && !canMore)}
      </div>
      <span className="muted">{more?.loading ? ko.common.loading : more?.has ? t.totalMore(total) : t.total(total)}</span>
      <span className="spacer" />
      <label className="pager-size">
        {t.size}
        <select
          value={size}
          onChange={(e) => {
            setPageSize(Number(e.target.value));
            setPage(0);
          }}
        >
          {PAGE_SIZES.map((n) => (
            <option key={n} value={n}>
              {t.sizeOption(n)}
            </option>
          ))}
        </select>
      </label>
    </nav>
  );
}
