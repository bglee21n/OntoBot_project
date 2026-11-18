// 화면의 날짜·시각 표기는 여기 한 곳에서 정한다 — `2026-11-13 22:18`(오늘이면 `오늘 22:18`).
// 브라우저 로캘(`toLocaleString`)마다 모양이 달라지지 않게 고정 형식을 쓴다. 시간대는 보는 사람의 것.

const pad = (n: number) => String(n).padStart(2, "0");

function parse(v: string | number | Date | null | undefined): Date | null {
  if (v === null || v === undefined || v === "") return null;
  const d = v instanceof Date ? v : new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

const ymd = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const hm = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

/** `2026-11-13 22:18` — 오늘이면 `오늘 22:18`. 값이 없으면 `—`. */
export function formatDateTime(v: string | number | Date | null | undefined): string {
  const d = parse(v);
  if (!d) return "—";
  return ymd(d) === ymd(new Date()) ? `오늘 ${hm(d)}` : `${ymd(d)} ${hm(d)}`;
}

/** `2026-11-13` */
export function formatDate(v: string | number | Date | null | undefined): string {
  const d = parse(v);
  return d ? ymd(d) : "—";
}

/** `22:18:05` — 같은 날 안의 순서가 중요한 곳(그래프 기준 시각 등). */
export function formatTime(v: string | number | Date | null | undefined): string {
  const d = parse(v);
  return d ? `${hm(d)}:${pad(d.getSeconds())}` : "—";
}
