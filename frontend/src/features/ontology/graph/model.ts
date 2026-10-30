// 스키마 그래프의 순수 계산 — 버전 상세 → 노드·엣지, 변경·이슈 표시, 깊이 포커스(Phase G1).
// 화면 상태(위치·선택)는 여기 두지 않는다.
import type { ChangeOut, IssueOut, LinkTypeOut, ObjectTypeOut, VersionDetail } from "../../../api/types";

export type ChangeKind = "added" | "removed" | "changed";

export interface TypeNodeData extends Record<string, unknown> {
  name: string;
  displayName: string;
  pk: string;
  title: string;
  propertyCount: number;
  hasPii: boolean;
  actions: string[];
  issues: IssueOut[];
  change: ChangeKind | null;
  deprecated: boolean;
  /** 기준 버전에만 있는 요소(이 버전에서 지워졌다) — 흐리게 그리고 편집할 수 없다. */
  ghost: boolean;
  /** 포커스 범위 밖이라 흐리게. */
  dimmed: boolean;
  /** 아직 펼치지 않은 이웃 수(포커스 중일 때만). */
  hiddenNeighbors: number;
  /** 이 노드로 펼쳐서 들어온 노드가 있다 — 접을 수 있다. */
  collapsible: boolean;
}

export interface LinkEdgeData extends Record<string, unknown> {
  name: string;
  displayName: string;
  cardinality: "one" | "many";
  sourceKind: string;
  issues: IssueOut[];
  change: ChangeKind | null;
  deprecated: boolean;
  ghost: boolean;
}

export interface GraphElements {
  types: TypeNodeData[];
  links: (LinkEdgeData & { from: string; to: string })[];
}

/**
 * 이슈 경로(`object_types.X…`·`link_types.l…`·`action_types.a…`·`functions.f`)가 가리키는 그래프 요소.
 * Action·Function 은 그래프 요소가 아니므로 대상 타입(Function 은 첫 내부 조회의 대상)에 붙인다.
 */
export function issueTarget(
  issue: IssueOut,
  actionTargets: Map<string, string>,
  functionTargets: Map<string, string> = new Map(),
): { kind: "type" | "link"; name: string } | null {
  const [head, name] = issue.path.split(".");
  if (!name) return null;
  if (head === "object_types") return { kind: "type", name };
  if (head === "link_types") return { kind: "link", name };
  if (head === "action_types") {
    const target = actionTargets.get(name);
    return target ? { kind: "type", name: target } : null;
  }
  if (head === "functions") {
    const target = functionTargets.get(name);
    return target ? { kind: "type", name: target } : null;
  }
  return null;
}

function changeMap(changes: ChangeOut[] | undefined) {
  const types = new Map<string, ChangeKind>();
  const links = new Map<string, ChangeKind>();
  for (const c of changes ?? []) {
    if (c.element === "object_type") types.set(c.path, c.kind);
    else if (c.element === "link_type") links.set(c.path, c.kind);
    else if (c.element === "property") {
      // 속성이 바뀐 타입은 '변경' — 타입 자체가 추가·삭제면 그쪽이 이긴다.
      const owner = c.path.split(".")[0] ?? c.path;
      if (!types.has(owner)) types.set(owner, "changed");
    }
  }
  return { types, links };
}

function typeData(t: ObjectTypeOut, extra: Partial<TypeNodeData>): TypeNodeData {
  return {
    name: t.api_name,
    displayName: t.display_name,
    pk: t.pk_property,
    title: t.title_property,
    propertyCount: t.properties.length,
    hasPii: t.properties.some((p) => p.pii_kind),
    actions: [],
    issues: [],
    change: null,
    deprecated: t.deprecated,
    ghost: false,
    dimmed: false,
    hiddenNeighbors: 0,
    collapsible: false,
    ...extra,
  };
}

function linkData(l: LinkTypeOut, extra: Partial<LinkEdgeData>) {
  return {
    from: l.from_type,
    to: l.to_type,
    name: l.api_name,
    displayName: l.display_name,
    cardinality: l.cardinality,
    sourceKind: l.source.kind,
    issues: [],
    change: null,
    deprecated: l.deprecated ?? false,
    ghost: false,
    ...extra,
  };
}

/** 버전 상세 → 그래프 요소. ``base`` 가 있으면 비교 결과를 입히고 지워진 요소를 유령으로 더한다. */
export function buildElements(v: VersionDetail, base?: VersionDetail, changes?: ChangeOut[]): GraphElements {
  const { types: typeChange, links: linkChange } = changeMap(base ? changes : undefined);
  const actionTargets = new Map(v.action_types.map((a) => [a.api_name, a.target_type]));
  const functionTargets = new Map(
    v.functions.flatMap((f) => (f.queries[0] ? [[f.api_name, f.queries[0].target] as const] : [])),
  );
  const types = v.object_types.map((t) =>
    typeData(t, {
      change: typeChange.get(t.api_name) ?? null,
      actions: v.action_types.filter((a) => a.target_type === t.api_name).map((a) => a.display_name),
    }),
  );
  const links = v.link_types.map((l) => linkData(l, { change: linkChange.get(l.api_name) ?? null }));
  if (base) {
    const have = new Set(types.map((t) => t.name));
    for (const t of base.object_types) {
      if (!have.has(t.api_name)) types.push(typeData(t, { change: "removed", ghost: true }));
    }
    const haveLinks = new Set(links.map((l) => l.name));
    const typeNames = new Set(types.map((t) => t.name));
    for (const l of base.link_types) {
      if (!haveLinks.has(l.api_name) && typeNames.has(l.from_type) && typeNames.has(l.to_type)) {
        links.push(linkData(l, { change: "removed", ghost: true }));
      }
    }
  }
  const byType = new Map(types.map((t) => [t.name, t]));
  const byLink = new Map(links.map((l) => [l.name, l]));
  for (const issue of v.issues) {
    const target = issueTarget(issue, actionTargets, functionTargets);
    if (target?.kind === "type") byType.get(target.name)?.issues.push(issue);
    else if (target?.kind === "link") byLink.get(target.name)?.issues.push(issue);
  }
  return { types, links };
}

/** 링크를 방향 없이 본 이웃 목록. 양 끝 타입이 그래프에 있는 링크만. */
export function adjacency(g: GraphElements): Map<string, Set<string>> {
  const adj = new Map<string, Set<string>>(g.types.map((t) => [t.name, new Set<string>()]));
  for (const l of g.links) {
    if (!adj.has(l.from) || !adj.has(l.to)) continue;
    adj.get(l.from)!.add(l.to);
    adj.get(l.to)!.add(l.from);
  }
  return adj;
}

/**
 * 포커스로 보이는 노드와 '누가 펼쳐서 들어왔는가'(확장 출처). ``start`` 에서 ``depth`` 홉까지 너비 우선.
 * 출처는 접기(collapse)가 그 노드로만 들어온 노드를 찾는 데 쓴다. 시작 노드의 출처는 null 이다.
 */
export function neighborhood(adj: Map<string, Set<string>>, start: string, depth: number): Map<string, string | null> {
  const shown = new Map<string, string | null>();
  if (!adj.has(start)) return shown;
  shown.set(start, null);
  let frontier = [start];
  for (let d = 0; d < depth; d++) {
    const next: string[] = [];
    for (const n of frontier) {
      for (const m of [...(adj.get(n) ?? [])].sort()) {
        if (!shown.has(m)) {
          shown.set(m, n);
          next.push(m);
        }
      }
    }
    frontier = next;
  }
  return shown;
}

/** 노드 하나를 1홉 펼친다 — 새로 보이는 이웃의 출처는 그 노드다. */
export function expand(adj: Map<string, Set<string>>, shown: Map<string, string | null>, node: string) {
  const out = new Map(shown);
  for (const m of [...(adj.get(node) ?? [])].sort()) {
    if (!out.has(m)) out.set(m, node);
  }
  return out;
}

/**
 * 노드를 접는다 — 그 노드를 출처로(자손까지) 들어온 노드를 숨긴다. 다만 접지 않는 다른 노드와도
 * 이어진 노드는 남긴다(다른 경로로도 연결돼 있다). 남은 노드의 출처는 그 이웃으로 바꾼다.
 */
export function collapse(adj: Map<string, Set<string>>, shown: Map<string, string | null>, node: string) {
  const sub = new Set<string>();
  let grew = true;
  while (grew) {
    grew = false;
    for (const [n, parent] of shown) {
      if (!sub.has(n) && parent !== null && (parent === node || sub.has(parent))) {
        sub.add(n);
        grew = true;
      }
    }
  }
  const out = new Map(shown);
  for (const n of sub) out.delete(n);
  // 남은 노드(접은 노드 제외)와 이어진 것은 되살린다 — 한 번만 본다(되살린 노드로 다시 퍼지지 않는다).
  const anchors = [...out.keys()].filter((n) => n !== node);
  for (const n of [...sub].sort()) {
    const via = anchors.find((a) => adj.get(a)?.has(n));
    if (via !== undefined) out.set(n, via);
  }
  return out;
}

/** 아직 보이지 않는 이웃 수. */
export function hiddenNeighborCount(adj: Map<string, Set<string>>, shown: Map<string, string | null>, node: string) {
  return [...(adj.get(node) ?? [])].filter((m) => !shown.has(m)).length;
}
