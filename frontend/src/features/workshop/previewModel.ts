// 워크숍 실시간 미리보기의 모델(Phase 18) — 세션 요소 → 요소 그래프·Action 정의서. 순수 함수.
// 거부한 요소는 빼고, 이름은 그대로(정확 일치) 잇는다 — 서버의 확정 검증·게시 합치기와 같은 규칙이다.
import type { ElementOut } from "../../api/types";

export interface PreviewType {
  name: string;
  displayName: string;
  status: string;
  properties: { name: string; displayName: string; dataType: string; status: string }[];
  // 그래프에만 있는 이름(속성·링크가 가리키지만 타입 요소가 없다) — 화면이 점선으로 그린다.
  implied: boolean;
}

export interface PreviewLink {
  name: string;
  displayName: string;
  from: string;
  to: string;
  status: string;
}

export interface PreviewAction {
  id: number;
  name: string;
  description: string;
  status: string;
  quotes: string[];
}

const s = (v: unknown): string => (v === null || v === undefined ? "" : String(v));

export function buildPreview(elements: ElementOut[]): {
  types: PreviewType[];
  links: PreviewLink[];
  actions: PreviewAction[];
} {
  const live = elements.filter((e) => e.status !== "rejected");
  const types = new Map<string, PreviewType>();
  const ensure = (name: string, implied: boolean) => {
    let t = types.get(name);
    if (!t) {
      t = { name, displayName: name, status: "proposed", properties: [], implied };
      types.set(name, t);
    }
    return t;
  };
  for (const e of live.filter((x) => x.kind === "object_type")) {
    const name = s(e.payload.api_name);
    if (!name) continue;
    const t = ensure(name, false);
    t.implied = false;
    t.displayName = s(e.payload.display_name) || name;
    t.status = e.status;
  }
  for (const e of live.filter((x) => x.kind === "property")) {
    const owner = s(e.payload.object_type);
    const name = s(e.payload.api_name);
    if (!owner || !name) continue;
    ensure(owner, true).properties.push({
      name,
      displayName: s(e.payload.display_name) || name,
      dataType: s(e.payload.data_type),
      status: e.status,
    });
  }
  const links: PreviewLink[] = [];
  for (const e of live.filter((x) => x.kind === "link_type")) {
    const from = s(e.payload.from_type);
    const to = s(e.payload.to_type);
    if (!from || !to) continue;
    ensure(from, true);
    ensure(to, true);
    links.push({
      name: s(e.payload.api_name),
      displayName: s(e.payload.display_name) || s(e.payload.api_name),
      from,
      to,
      status: e.status,
    });
  }
  const actions = live
    .filter((x) => x.kind === "action_candidate")
    .map((e) => ({
      id: e.id,
      name: s(e.payload.name),
      description: s(e.payload.description),
      status: e.status,
      quotes: e.evidence.map((ev) => ev.quote),
    }));
  return { types: [...types.values()].sort((a, b) => a.name.localeCompare(b.name)), links, actions };
}
