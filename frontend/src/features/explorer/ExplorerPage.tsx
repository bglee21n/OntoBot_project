import "@xyflow/react/dist/style.css";

import { useQuery } from "@tanstack/react-query";
import {
  Background,
  Controls,
  type Edge,
  Handle,
  MarkerType,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import { createContext, type FormEvent, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";

import { api } from "../../api/client";
import type { EditHistoryOut, ExplorerLimitsOut, NeighborsOut, SearchHitOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { ObjectCardPanel } from "../chat/AnswerView";
import { autoLayout } from "../ontology/graph/layout";
import { addNeighbors, collapse, frontier, type GraphState, hiddenCount, keyOf, type ObjNode, start } from "./model";

const t = ko.explorer;

/** 채팅 근거 카드에서 넘어올 때의 시작 객체(라우터 상태 — URL 에 pk 를 넣지 않는다). */
export interface ExploreStart {
  type: string;
  typeDisplayName: string;
  pk: string;
  title: string;
}

type FlowNode = Node<{ obj: ObjNode; hidden: number | null; expanded: boolean; root: boolean }, "obj">;

const NodeActions = createContext<{ expand: (key: string) => void; collapse: (key: string) => void } | null>(null);

function ObjNodeView({ data, selected }: NodeProps<FlowNode>) {
  const actions = useContext(NodeActions);
  const { obj, hidden, expanded, root } = data;
  return (
    <div className={["schema-node", "obj-node", selected ? "selected" : "", root ? "root" : ""].join(" ")}>
      <Handle type="target" position={Position.Left} />
      <strong>{obj.title}</strong>
      <div className="schema-node-meta muted">
        {obj.typeDisplayName} · <code>{obj.pk}</code>
      </div>
      <div className="schema-node-expand nodrag">
        {expanded && !root && (
          <button type="button" className="link" title={t.collapse} onClick={() => actions?.collapse(obj.key)}>
            −
          </button>
        )}
        {!expanded && (
          <button type="button" className="link" title={t.expand} onClick={() => actions?.expand(obj.key)}>
            +
          </button>
        )}
        {expanded && hidden !== null && hidden > 0 && <span className="muted">{t.more(hidden)}</span>}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const NODE_TYPES = { obj: ObjNodeView };

export function ExplorerPage() {
  return (
    <ReactFlowProvider>
      <Explorer />
    </ReactFlowProvider>
  );
}

function Explorer() {
  const location = useLocation();
  const flow = useReactFlow();
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [graph, setGraph] = useState<GraphState | null>(null);
  const [root, setRoot] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [depth, setDepth] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [positions, setPositions] = useState<Map<string, { x: number; y: number }>>(new Map());
  const typeNames = useRef(new Map<string, string>());
  // 노드 안 단추(컨텍스트)가 지금 그래프를 보게 한다 — 렌더 중이 아니라 커밋 뒤에 맞춘다.
  const graphRef = useRef<GraphState | null>(null);
  useEffect(() => {
    graphRef.current = graph;
  }, [graph]);

  const limits = useQuery({
    queryKey: ["explorer-limits"],
    queryFn: () => api.get<ExplorerLimitsOut>("/objects/explorer-limits"),
  });
  const maxDepth = limits.data?.max_depth ?? 3;
  const maxNodes = limits.data?.max_nodes ?? 200;
  const hits = useQuery({
    queryKey: ["object-search", submitted],
    queryFn: () => api.get<SearchHitOut[]>(`/objects/search?q=${encodeURIComponent(submitted)}`),
    enabled: submitted.length > 0,
  });

  const fetchOne = useCallback(
    async (s: GraphState, key: string): Promise<GraphState> => {
      const n = s.nodes.get(key);
      if (!n) return s;
      const res = await api.get<NeighborsOut>(
        `/objects/${encodeURIComponent(n.type)}/${encodeURIComponent(n.pk)}/neighbors?limit=${maxNodes}`,
      );
      for (const g of res.groups) typeNames.current.set(g.other_type, g.other_display_name);
      return addNeighbors(s, key, res, typeNames.current, maxNodes);
    },
    [maxNodes],
  );

  /** 루트에서 깊이 d 까지 1홉을 너비 우선으로 되풀이한다(상한에 닿으면 멈춘다). */
  const grow = useCallback(
    async (s: GraphState, rootKey: string, d: number) => {
      setBusy(true);
      setError(null);
      try {
        let cur = s;
        for (let level = 0; level < d; level++) {
          const next = frontier(cur, rootKey, d);
          if (!next.length || cur.nodes.size >= maxNodes) break;
          for (const k of next) cur = await fetchOne(cur, k);
        }
        setGraph(cur);
      } catch (e) {
        setError(e);
        setGraph(s);
      } finally {
        setBusy(false);
      }
    },
    [fetchOne, maxNodes],
  );

  const open = useCallback(
    (h: ExploreStart) => {
      typeNames.current.set(h.type, h.typeDisplayName);
      const s = start(h);
      const key = keyOf(h.type, h.pk);
      setRoot(key);
      setSelected(key);
      setGraph(s);
      void grow(s, key, depth);
    },
    [depth, grow],
  );

  // 근거 카드에서 넘어온 객체로 시작한다(한 번만).
  const startedFrom = useRef<unknown>(null);
  useEffect(() => {
    const st = location.state as { explore?: ExploreStart } | null;
    if (st?.explore && startedFrom.current !== st && limits.data) {
      startedFrom.current = st;
      open(st.explore);
    }
  }, [location.state, limits.data, open]);

  const actions = useMemo(
    () => ({
      expand: (key: string) => {
        const s = graphRef.current;
        if (!s) return;
        setBusy(true);
        setError(null);
        fetchOne(s, key)
          .then(setGraph)
          .catch(setError)
          .finally(() => setBusy(false));
      },
      collapse: (key: string) => setGraph((s) => (s ? collapse(s, key) : s)),
    }),
    [fetchOne],
  );

  // 노드 집합이 바뀌면 자동 배치(elkjs, 번들).
  const nodeKeys = graph ? [...graph.nodes.keys()].sort().join("|") : "";
  useEffect(() => {
    if (!graph) return;
    let alive = true;
    void autoLayout(
      [...graph.nodes.keys()],
      [...graph.edges.values()].map((e) => ({ from: e.from, to: e.to })),
    ).then((p) => {
      if (alive) {
        setPositions(p);
        requestAnimationFrame(() => void flow.fitView({ padding: 0.2, duration: 200 }));
      }
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 노드 집합이 바뀔 때만 다시 배치한다
  }, [nodeKeys]);

  const nodes: FlowNode[] = graph
    ? [...graph.nodes.values()].map((obj) => ({
        id: obj.key,
        type: "obj",
        position: positions.get(obj.key) ?? { x: 0, y: 0 },
        selected: obj.key === selected,
        data: {
          obj,
          hidden: hiddenCount(graph, obj.key),
          expanded: graph.totals.has(obj.key),
          root: obj.key === root,
        },
      }))
    : [];
  const edges: Edge[] = graph
    ? [...graph.edges.values()].map((e) => ({
        id: e.id,
        source: e.from,
        target: e.to,
        label: e.label,
        markerEnd: { type: MarkerType.ArrowClosed },
      }))
    : [];

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(q.trim());
  };
  const sel = selected && graph ? graph.nodes.get(selected) : undefined;

  return (
    <section className="stack">
      <h1>{t.title}</h1>
      <p className="muted">{t.intro}</p>
      <div className="explorer">
        <aside className="stack explorer-search">
          <form className="row" onSubmit={submit}>
            <input aria-label={t.search} placeholder={t.searchHint} value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} />
            <button type="submit" disabled={!q.trim()}>
              {t.searchButton}
            </button>
          </form>
          {hits.isError && <ErrorBox error={hits.error} />}
          {hits.data?.length === 0 && <p className="muted">{t.noHits}</p>}
          <nav className="chat-list">
            {hits.data?.map((h) => (
              <button
                type="button"
                key={keyOf(h.type, h.pk)}
                className={`link hit${keyOf(h.type, h.pk) === root ? " active" : ""}`}
                onClick={() => open({ type: h.type, typeDisplayName: h.type_display_name, pk: h.pk, title: h.title })}
              >
                {h.title} <span className="muted">· {h.type_display_name}</span>
              </button>
            ))}
          </nav>
        </aside>

        <div className="stack explorer-main">
          <div className="row schema-toolbar">
            <label>
              {t.depth(depth)}
              <input
                type="range"
                min={1}
                max={maxDepth}
                value={Math.min(depth, maxDepth)}
                onChange={(e) => setDepth(Number(e.target.value))}
              />
            </label>
            <button type="button" disabled={!graph || !root || busy} onClick={() => graph && root && void grow(graph, root, depth)}>
              {t.grow}
            </button>
            <button
              type="button"
              className="link"
              disabled={!graph || !root}
              onClick={() => {
                const n = root && graph?.nodes.get(root);
                if (n) open(n);
              }}
            >
              {t.reset}
            </button>
            <span className="spacer" />
            {busy && <span className="muted">{ko.common.loading}</span>}
            {graph && <span className="muted">{t.count(graph.nodes.size, maxNodes)}</span>}
          </div>
          {graph?.capped && <p className="notice warn">{t.capped(maxNodes)}</p>}
          {error !== null && <ErrorBox error={error} />}
          <div className="schema-canvas">
            {!graph && <p className="schema-empty muted">{t.empty}</p>}
            <NodeActions.Provider value={actions}>
              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={NODE_TYPES}
                nodesConnectable={false}
                nodesDraggable
                onNodeClick={(_, n) => setSelected(n.id)}
                onNodeDoubleClick={(_, n) => graph && !graph.totals.has(n.id) && actions.expand(n.id)}
                fitView
                proOptions={{ hideAttribution: true }}
              >
                <Background />
                <Controls showInteractive={false} />
              </ReactFlow>
            </NodeActions.Provider>
          </div>
        </div>

        {sel && (
          <div className="stack schema-panel">
            <ObjectCardPanel
              key={sel.key}
              selection={{ target: `o:${sel.type}/${sel.pk}`, title: sel.title, fields: [] }}
              onClose={() => setSelected(null)}
              explore={false}
            />
            <EditHistory type={sel.type} pk={sel.pk} />
          </div>
        )}
      </div>
    </section>
  );
}

function EditHistory({ type, pk }: { type: string; pk: string }) {
  const history = useQuery({
    queryKey: ["object", type, pk, "edits"],
    queryFn: () => api.get<EditHistoryOut[]>(`/objects/${encodeURIComponent(type)}/${encodeURIComponent(pk)}/edits`),
  });
  return (
    <div className="card stack">
      <strong>{t.history}</strong>
      {history.isError && <ErrorBox error={history.error} />}
      {history.data?.length === 0 && <p className="muted">{t.noHistory}</p>}
      {history.data && history.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>{t.property}</th>
              <th>{t.value}</th>
              <th>{t.by}</th>
              <th>{t.state}</th>
            </tr>
          </thead>
          <tbody>
            {history.data.map((h) => (
              <tr key={h.edit_id}>
                <td>{h.display_name}</td>
                <td>
                  {h.source_value} → <strong>{h.value}</strong>
                </td>
                <td>
                  {h.edited_by ?? "—"}
                  <div className="muted">{new Date(h.edited_at).toLocaleString("ko-KR")}</div>
                </td>
                <td>{h.cleared_at ? t.cleared(new Date(h.cleared_at).toLocaleString("ko-KR")) : t.active}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
