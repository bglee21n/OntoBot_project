// 자동 배치(elkjs, 번들 — 외부 호출 없음). 저장된 좌표가 있으면 그것이 이긴다.
import ELK from "elkjs/lib/elk.bundled.js";

export interface Point {
  x: number;
  y: number;
}

export const NODE_WIDTH = 220;
export const NODE_HEIGHT = 96;

const elk = new ELK();

export async function autoLayout(nodes: string[], edges: { from: string; to: string }[]): Promise<Map<string, Point>> {
  const known = new Set(nodes);
  const graph = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "48",
      "elk.layered.spacing.nodeNodeBetweenLayers": "96",
    },
    children: nodes.map((id) => ({ id, width: NODE_WIDTH, height: NODE_HEIGHT })),
    edges: edges
      .filter((e) => known.has(e.from) && known.has(e.to) && e.from !== e.to)
      .map((e, i) => ({ id: `e${i}`, sources: [e.from], targets: [e.to] })),
  });
  return new Map((graph.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]));
}

/** 저장 좌표 우선, 없는 노드만 자동 배치 좌표. */
export function mergePositions(auto: Map<string, Point>, saved: Map<string, Point>): Map<string, Point> {
  const out = new Map(auto);
  for (const [name, p] of saved) {
    if (out.has(name)) out.set(name, p);
  }
  return out;
}
