// 인스턴스 그래프의 화면 상태(순수). 서버는 1홉만 주고, 깊이 N 은 여기서 1홉을 너비 우선으로 되풀이한다.
// 키(`타입/pk`)는 화면 메모리에만 둔다 — URL 에 pk 를 넣지 않는다(개인정보 규칙).
import type { NeighborsOut } from "../../api/types";

export interface ObjNode {
  key: string;
  type: string;
  typeDisplayName: string;
  pk: string;
  title: string;
  // 누가 펼쳐서 들어왔는가(시작 객체는 null) — 접기가 이 기록으로 하위 노드를 찾는다.
  via: string | null;
  depth: number;
}

export interface ObjEdge {
  id: string;
  from: string;
  to: string;
  label: string;
}

export interface GraphState {
  nodes: Map<string, ObjNode>;
  edges: Map<string, ObjEdge>;
  // 펼친 노드 → 읽을 수 있는 이웃 수(서버 개수). 펼치지 않은 노드는 없다.
  totals: Map<string, { count: number; truncated: boolean }>;
  // 노드 상한에 걸려 넣지 못한 이웃이 있었는가.
  capped: boolean;
}

export const keyOf = (type: string, pk: string) => `${type}/${pk}`;

export function start(n: { type: string; typeDisplayName: string; pk: string; title: string }): GraphState {
  const key = keyOf(n.type, n.pk);
  return {
    nodes: new Map([[key, { ...n, key, via: null, depth: 0 }]]),
    edges: new Map(),
    totals: new Map(),
    capped: false,
  };
}

/** 노드 하나의 1홉 결과를 더한다. 상한(maxNodes)을 넘는 이웃은 넣지 않고 capped 로 알린다. */
export function addNeighbors(
  s: GraphState,
  fromKey: string,
  res: NeighborsOut,
  typeNames: Map<string, string>,
  maxNodes: number,
): GraphState {
  const from = s.nodes.get(fromKey);
  if (!from) return s;
  const nodes = new Map(s.nodes);
  const edges = new Map(s.edges);
  const labels = new Map(res.groups.map((g) => [`${g.link_type}:${g.direction}`, g.link_display_name]));
  let capped = s.capped;
  for (const it of res.items) {
    const key = keyOf(it.type, it.pk);
    if (!nodes.has(key)) {
      if (nodes.size >= maxNodes) {
        capped = true;
        continue;
      }
      nodes.set(key, {
        key,
        type: it.type,
        typeDisplayName: typeNames.get(it.type) ?? it.type,
        pk: it.pk,
        title: it.title,
        via: fromKey,
        depth: from.depth + 1,
      });
    }
    const [a, b] = it.direction === "out" ? [fromKey, key] : [key, fromKey];
    const id = `${it.link_type}:${a}->${b}`;
    edges.set(id, { id, from: a, to: b, label: labels.get(`${it.link_type}:${it.direction}`) ?? it.link_type });
  }
  const totals = new Map(s.totals);
  totals.set(fromKey, {
    count: res.groups.reduce((n, g) => n + g.total, 0),
    truncated: res.groups.some((g) => g.truncated),
  });
  return { nodes, edges, totals, capped };
}

/** 화면에 있는, key 와 엣지로 이어진 이웃 수. */
function shownNeighbors(s: GraphState, key: string): number {
  const seen = new Set<string>();
  for (const e of s.edges.values()) {
    if (e.from === key) seen.add(e.to);
    if (e.to === key) seen.add(e.from);
  }
  return seen.size;
}

/** "+N" — 펼쳤지만 상한 등으로 아직 화면에 없는 이웃 수. 펼치지 않았으면 null(모른다). */
export function hiddenCount(s: GraphState, key: string): number | null {
  const t = s.totals.get(key);
  if (!t) return null;
  return Math.max(0, t.count - shownNeighbors(s, key));
}

/**
 * 접기 — 이 노드를 통해서만 들어온 하위 노드를 지운다. 남는 노드와 다른 엣지로도 이어진 노드는 남기고,
 * 그 노드를 거쳐 들어온 것도 함께 남는다.
 */
export function collapse(s: GraphState, key: string): GraphState {
  const below = new Set<string>();
  const walk = (k: string) => {
    for (const n of s.nodes.values()) {
      if (n.via === k && !below.has(n.key)) {
        below.add(n.key);
        walk(n.key);
      }
    }
  };
  walk(key);
  // 남는 쪽(below 밖, key 제외)과 이어진 노드를 below 에서 꺼낸다 — 꺼낸 노드와 이어진 것도 다시 본다.
  let changed = true;
  while (changed) {
    changed = false;
    for (const e of s.edges.values()) {
      const pairs: [string, string][] = [
        [e.from, e.to],
        [e.to, e.from],
      ];
      for (const [x, y] of pairs) {
        if (below.has(x) && !below.has(y) && y !== key) {
          below.delete(x);
          changed = true;
        }
      }
    }
  }
  const nodes = new Map([...s.nodes].filter(([k]) => !below.has(k)));
  for (const [k, n] of nodes) {
    if (n.via !== null && !nodes.has(n.via)) nodes.set(k, { ...n, via: key });
  }
  const edges = new Map([...s.edges].filter(([, e]) => nodes.has(e.from) && nodes.has(e.to)));
  const totals = new Map([...s.totals].filter(([k]) => nodes.has(k) && k !== key));
  return { nodes, edges, totals, capped: false };
}

/** 펼치지 않은 노드 중 depth 가 maxDepth 보다 얕은 것 — 깊이 지정 확장의 다음 차례. */
export function frontier(s: GraphState, rootKey: string, maxDepth: number): string[] {
  const root = s.nodes.get(rootKey);
  if (!root) return [];
  // 루트로부터의 거리(엣지 기준, 너비 우선).
  const dist = new Map([[rootKey, 0]]);
  const queue = [rootKey];
  while (queue.length) {
    const k = queue.shift()!;
    for (const e of s.edges.values()) {
      const other = e.from === k ? e.to : e.to === k ? e.from : null;
      if (other && !dist.has(other)) {
        dist.set(other, dist.get(k)! + 1);
        queue.push(other);
      }
    }
  }
  return [...dist].filter(([k, d]) => d < maxDepth && !s.totals.has(k)).map(([k]) => k);
}
