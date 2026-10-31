import "@xyflow/react/dist/style.css";

import { Background, type Edge, MarkerType, type Node, ReactFlow, ReactFlowProvider, useNodesState } from "@xyflow/react";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import type { ElementOut } from "../../api/types";
import { ko } from "../../i18n/ko";
import { autoLayout, type Point } from "../ontology/graph/layout";
import { buildPreview, type PreviewAction, type PreviewType } from "./previewModel";

const t = ko.workshop.preview;
type Tab = "review" | "graph" | "actions";

/**
 * 실시간 미리보기(Phase 18) — 검토 목록·요소 그래프·Action 정의서. 요소가 바뀌면(답 한 번마다) 다시 그린다.
 * 그래프는 거부하지 않은 요소로 그린 "이대로 게시하면 생길 모양"이다. 확정 전 요소는 점선·흐리게.
 */
export function WorkshopPreview({ elements, review }: { elements: ElementOut[]; review: ReactNode }) {
  const [tab, setTab] = useState<Tab>("review");
  const preview = useMemo(() => buildPreview(elements), [elements]);
  return (
    <div className="stack">
      <div className="row tabs" role="tablist">
        {(["review", "graph", "actions"] as const).map((x) => (
          <button
            key={x}
            type="button"
            role="tab"
            aria-selected={tab === x}
            className={tab === x ? "tab active" : "tab"}
            onClick={() => setTab(x)}
          >
            {t.tabs[x]}
            {x === "actions" && preview.actions.length > 0 && ` (${preview.actions.length})`}
          </button>
        ))}
      </div>
      {tab === "review" && review}
      {tab === "graph" &&
        (preview.types.length === 0 ? (
          <p className="muted">{t.noGraph}</p>
        ) : (
          <ReactFlowProvider>
            <ElementGraph types={preview.types} links={preview.links} />
          </ReactFlowProvider>
        ))}
      {tab === "actions" && <ActionSheet actions={preview.actions} />}
    </div>
  );
}

const pending = (status: string) => status === "proposed";

function ElementGraph({ types, links }: { types: PreviewType[]; links: ReturnType<typeof buildPreview>["links"] }) {
  const [pos, setPos] = useState<Map<string, Point>>(new Map());
  const key = useMemo(() => JSON.stringify([types.map((x) => x.name), links.map((l) => [l.from, l.to])]), [types, links]);
  useEffect(() => {
    let alive = true;
    void autoLayout(
      types.map((x) => x.name),
      links.map((l) => ({ from: l.from, to: l.to })),
    ).then((p) => alive && setPos(p));
    return () => {
      alive = false;
    };
    // 이름·연결이 바뀔 때만 다시 배치한다(값만 바뀌면 자리를 그대로 둔다).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const built: Node[] = useMemo(() => types.map((x) => ({
    id: x.name,
    position: pos.get(x.name) ?? { x: 0, y: 0 },
    data: {
      label: (
        <div className="ws-node">
          <strong>{x.displayName}</strong> <code>{x.name}</code>
          {x.implied && <div className="muted">{t.implied}</div>}
          <ul>
            {x.properties.map((p) => (
              <li key={p.name} className={pending(p.status) ? "muted" : undefined}>
                {p.displayName} <span className="muted">{p.dataType}</span>
              </li>
            ))}
          </ul>
        </div>
      ),
    },
    className: x.implied ? "ws-node-implied" : pending(x.status) ? "ws-node-pending" : undefined,
    draggable: true,
  })), [types, pos]);
  // 노드 크기 재기·끌기는 xyflow 가 변경으로 알린다 — 받아 두지 않으면 노드가 보이지 않고 엣지도 없다.
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  useEffect(() => setNodes(built), [built, setNodes]);
  const edges: Edge[] = links.map((l, i) => ({
    id: `l${i}`,
    source: l.from,
    target: l.to,
    label: l.displayName,
    markerEnd: { type: MarkerType.ArrowClosed },
    style: pending(l.status) ? { strokeDasharray: "4 3" } : undefined,
  }));
  return (
    <div className="ws-graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        fitView
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background />
      </ReactFlow>
      <p className="muted">{t.graphHint}</p>
    </div>
  );
}

function ActionSheet({ actions }: { actions: PreviewAction[] }) {
  if (actions.length === 0) return <p className="muted">{t.noActions}</p>;
  return (
    <div className="stack">
      <p className="muted">{t.actionsHint}</p>
      {actions.map((a) => (
        <article key={a.id} className="card stack ws-sheet">
          <div className="row">
            <strong>{a.name}</strong>
            <span className="spacer" />
            <span className="badge">{ko.workshop.elementStatus[a.status] ?? a.status}</span>
          </div>
          <dl>
            <dt>{t.what}</dt>
            <dd>{a.description || "—"}</dd>
            <dt>{t.said}</dt>
            <dd>
              {a.quotes.length ? (
                <ul>
                  {a.quotes.map((q) => (
                    <li key={q}>“{q}”</li>
                  ))}
                </ul>
              ) : (
                "—"
              )}
            </dd>
            <dt>{t.todo}</dt>
            <dd className="muted">{t.todoText}</dd>
          </dl>
        </article>
      ))}
    </div>
  );
}
