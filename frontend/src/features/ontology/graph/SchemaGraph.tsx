import "@xyflow/react/dist/style.css";

import {
  Background,
  type Connection,
  Controls,
  type Edge,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../../../api/client";
import type { LinkTypeDef, LinkTypeOut, ObjectTypeCreate, ObjectTypePatch, VersionDetail } from "../../../api/types";
import { ErrorBox } from "../../../components/ErrorBox";
import { ko } from "../../../i18n/ko";
import { LinkTypeForm, ObjectTypeForm, ObjectTypeHeadForm } from "../forms";
import { ObjectTypeCard } from "../ObjectTypeCard";
import { useDiff, useLayout, useSaveLayout, useVersion, useVersionMutation, useVersions, V } from "../queries";
import { type DeleteTarget, DeleteDialog } from "./DeleteDialog";
import { autoLayout, mergePositions, type Point } from "./layout";
import {
  adjacency,
  buildElements,
  collapse,
  expand,
  hiddenNeighborCount,
  type LinkEdgeData,
  neighborhood,
} from "./model";
import { NodeActions, type TypeNode, TypeNodeView } from "./TypeNode";

type Selection =
  | { kind: "type"; name: string }
  | { kind: "link"; name: string }
  | { kind: "newType"; at: Point }
  | { kind: "newLink"; from: string; to: string };

const NODE_TYPES = { type: TypeNodeView };

function linkDef(l: LinkTypeOut): LinkTypeDef {
  const def: LinkTypeDef & { id?: number } = { ...l };
  delete def.id;
  return def;
}
const MAX_DEPTH = 3;
const SAVE_DELAY_MS = 500;

export function SchemaGraph(props: { v: VersionDetail; editable: boolean; canSaveLayout: boolean }) {
  return (
    <ReactFlowProvider>
      <GraphCanvas {...props} />
    </ReactFlowProvider>
  );
}

function GraphCanvas({ v, editable, canSaveLayout }: { v: VersionDetail; editable: boolean; canSaveLayout: boolean }) {
  const flow = useReactFlow();
  const versions = useVersions();
  const [base, setBase] = useState<number | null>(v.parent_id ?? null);
  const baseDetail = useVersion(base ?? 0, base !== null);
  const parent = useVersion(v.parent_id ?? 0, v.parent_id != null);
  const diff = useDiff(v.id, base);
  const layout = useLayout(v.id);
  const saveLayout = useSaveLayout(v.id);

  const elements = useMemo(
    () => buildElements(v, base !== null ? baseDetail.data : undefined, diff.data),
    [v, base, baseDetail.data, diff.data],
  );
  const adj = useMemo(() => adjacency(elements), [elements]);

  // ---- 포커스(깊이에 따른 확장·축소). null 이면 전체를 본다.
  const [focus, setFocus] = useState<{ root: string; depth: number } | null>(null);
  const [shown, setShown] = useState<Map<string, string | null> | null>(null);
  const [hideOutside, setHideOutside] = useState(false);
  const startFocus = (root: string, depth: number) => {
    setFocus({ root, depth });
    setShown(neighborhood(adj, root, depth));
  };
  const actions = useMemo(
    () =>
      shown
        ? {
            expand: (name: string) => setShown((s) => (s ? expand(adj, s, name) : s)),
            collapse: (name: string) => setShown((s) => (s ? collapse(adj, s, name) : s)),
          }
        : null,
    [adj, shown],
  );

  const [selection, setSelection] = useState<Selection | null>(null);
  const [deleting, setDeleting] = useState<DeleteTarget | null>(null);

  // ---- 노드: 위치는 노드 상태가 정본(끌기), 처음 보는 노드만 자동 배치·저장 좌표로 놓는다.
  const [nodes, setNodes, onNodesChange] = useNodesState<TypeNode>([]);
  const placed = useRef(new Map<string, Point>());
  const namesKey = elements.types.map((t) => t.name).join("|");
  const linksKey = elements.links.map((l) => `${l.from}>${l.to}`).join("|");
  const [layoutTick, setLayoutTick] = useState(0);
  const [resetTick, setResetTick] = useState(0);
  const fitted = useRef(false);

  useEffect(() => {
    if (layout.isPending) return;
    let cancelled = false;
    const names = namesKey ? namesKey.split("|") : [];
    const edges = (linksKey ? linksKey.split("|") : []).map((k) => {
      const [from = "", to = ""] = k.split(">");
      return { from, to };
    });
    const saved = new Map((layout.data?.nodes ?? []).map((n) => [n.name, { x: n.x, y: n.y }]));
    void autoLayout(names, edges).then((auto) => {
      if (cancelled) return;
      const merged = mergePositions(auto, saved);
      for (const [name, p] of merged) {
        if (!placed.current.has(name)) placed.current.set(name, p);
      }
      setNodes((prev) => prev.map((n) => ({ ...n, position: placed.current.get(n.id) ?? n.position })));
      setLayoutTick((t) => t + 1);
      // 처음 배치(와 자동 정렬) 뒤에만 화면을 맞춘다 — 편집할 때마다 시점이 튀지 않게.
      if (!fitted.current) {
        fitted.current = true;
        window.setTimeout(() => void flow.fitView({ padding: 0.2, maxZoom: 1 }), 50);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [namesKey, linksKey, layout.isPending, layout.data, setNodes, resetTick, flow]);

  useEffect(() => {
    setNodes((prev) => {
      const old = new Map(prev.map((n) => [n.id, n]));
      return elements.types
        .filter((t) => !shown || !hideOutside || shown.has(t.name))
        .map((t) => {
          const o = old.get(t.name);
          const inFocus = !shown || shown.has(t.name);
          return {
            ...o,
            id: t.name,
            type: "type" as const,
            position: o?.position ?? placed.current.get(t.name) ?? { x: 0, y: 0 },
            draggable: true,
            connectable: editable && !t.ghost,
            deletable: editable && !t.ghost,
            data: {
              ...t,
              dimmed: !inFocus,
              hiddenNeighbors: shown && inFocus ? hiddenNeighborCount(adj, shown, t.name) : 0,
              collapsible: shown ? [...shown.values()].includes(t.name) : false,
            },
          };
        });
    });
  }, [elements, shown, hideOutside, adj, editable, setNodes, layoutTick]);

  const visible = new Set(nodes.map((n) => n.id));
  const edges: Edge<LinkEdgeData>[] = elements.links
    .filter((l) => visible.has(l.from) && visible.has(l.to))
    .map((l) => {
      const dim = shown !== null && !(shown.has(l.from) && shown.has(l.to));
      const color = l.issues.length
        ? "var(--danger)"
        : l.change === "added"
          ? "var(--ok)"
          : l.change === "changed"
            ? "var(--warn)"
            : l.ghost || l.deprecated
              ? "var(--muted)"
              : "#52606d";
      const dash = l.ghost || l.deprecated ? "2 4" : l.sourceKind === "join_mapping" ? "6 3" : undefined;
      return {
        id: l.name,
        source: l.from,
        target: l.to,
        data: l,
        label: `${l.displayName} (${l.cardinality === "one" ? "1" : "N"})`,
        deletable: editable && !l.ghost,
        selected: selection?.kind === "link" && selection.name === l.name,
        markerEnd: { type: MarkerType.ArrowClosed, color },
        style: { stroke: color, strokeDasharray: dash, strokeWidth: 1.6, opacity: dim ? 0.25 : 1 },
        labelStyle: { fontSize: 11, opacity: dim ? 0.3 : 1 },
      };
    });

  // ---- 위치 저장(끌기가 끝나고 잠시 뒤 통째로).
  const saveTimer = useRef<number | undefined>(undefined);
  const scheduleSave = useCallback(() => {
    if (!canSaveLayout) return;
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      const ghosts = new Set(elements.types.filter((t) => t.ghost).map((t) => t.name));
      const all = [...placed.current].filter(([name]) => !ghosts.has(name));
      saveLayout.mutate(all.map(([name, p]) => ({ kind: "object_type" as const, name, x: p.x, y: p.y })));
    }, SAVE_DELAY_MS);
  }, [canSaveLayout, elements.types, saveLayout]);
  useEffect(() => () => window.clearTimeout(saveTimer.current), []);

  const resetLayout = () => {
    if (!window.confirm(ko.ontology.graph.autoLayoutConfirm)) return;
    placed.current.clear();
    fitted.current = false;
    setNodes((prev) => prev.map((n) => ({ ...n, position: { x: 0, y: 0 } })));
    if (canSaveLayout) saveLayout.mutate([]);
    setResetTick((t) => t + 1);
  };

  // ---- 편집
  const typeNames = v.object_types.map((t) => t.api_name);
  const fkChoices = (from: string) => v.object_types.find((t) => t.api_name === from)?.properties.map((p) => p.api_name) ?? [];
  const addType = useVersionMutation(v.id, (body: ObjectTypeCreate) => api.post(`${V}/${v.id}/object-types`, body));
  const addLink = useVersionMutation(v.id, (body: LinkTypeDef) => api.post(`${V}/${v.id}/link-types`, body));
  const patchType = useVersionMutation(v.id, (a: { id: number; body: ObjectTypePatch }) =>
    api.patch(`${V}/${v.id}/object-types/${a.id}`, a.body),
  );
  const patchLink = useVersionMutation(v.id, (a: { id: number; body: LinkTypeDef }) =>
    api.patch(`${V}/${v.id}/link-types/${a.id}`, a.body),
  );
  const inParent = (target: DeleteTarget) =>
    (target.kind === "object_type" ? parent.data?.object_types : parent.data?.link_types)?.some(
      (x) => x.api_name === target.name,
    ) ?? false;

  // 새 타입은 더블클릭한 자리에, 도구 막대로 만들면 지금 노드들의 오른쪽에 놓는다(자동 배치가 겹치지 않게).
  const newTypeAt = (): Point => {
    const ps = [...placed.current.values()];
    if (ps.length === 0) return { x: 0, y: 0 };
    return { x: Math.max(...ps.map((p) => p.x)) + 280, y: Math.min(...ps.map((p) => p.y)) };
  };

  const onConnect = (c: Connection) => {
    if (editable && c.source && c.target) setSelection({ kind: "newLink", from: c.source, to: c.target });
  };
  const selectedType = selection?.kind === "type" ? v.object_types.find((t) => t.api_name === selection.name) : undefined;
  const selectedLink = selection?.kind === "link" ? v.link_types.find((l) => l.api_name === selection.name) : undefined;
  const selectedGhost =
    (selection?.kind === "type" || selection?.kind === "link") && !selectedType && !selectedLink;
  const others = (versions.data ?? []).filter((x) => x.id !== v.id);
  const mutationError = addType.error ?? addLink.error ?? patchType.error ?? patchLink.error;

  return (
    <NodeActions.Provider value={actions}>
      <div className="schema-graph">
        <div className="row schema-toolbar">
          {editable && (
            <button type="button" onClick={() => setSelection({ kind: "newType", at: newTypeAt() })}>
              {ko.ontology.graph.addType}
            </button>
          )}
          <button type="button" className="link" onClick={() => void flow.fitView({ padding: 0.2 })}>
            {ko.ontology.graph.fit}
          </button>
          <button type="button" className="link" onClick={resetLayout}>
            {ko.ontology.graph.autoLayout}
          </button>
          <label className="row">
            {ko.ontology.graph.compareWith}
            <select value={base ?? ""} onChange={(e) => setBase(e.target.value ? Number(e.target.value) : null)}>
              <option value="">{ko.ontology.graph.noCompare}</option>
              {others.map((x) => (
                <option key={x.id} value={x.id}>
                  {ko.ontology.version(x.version_no)} ({ko.status[x.status]})
                </option>
              ))}
            </select>
          </label>
          <span className="spacer" />
          <label className="row">
            {ko.ontology.graph.depth}
            <input
              type="range"
              min={1}
              max={MAX_DEPTH}
              value={focus?.depth ?? 1}
              disabled={!focus}
              onChange={(e) => focus && startFocus(focus.root, Number(e.target.value))}
            />
            {focus?.depth ?? "—"}
          </label>
          <label className="check">
            <input type="checkbox" checked={hideOutside} disabled={!focus} onChange={(e) => setHideOutside(e.target.checked)} />
            {ko.ontology.graph.hideOutside}
          </label>
          {focus ? (
            <button
              type="button"
              className="link"
              onClick={() => {
                setFocus(null);
                setShown(null);
              }}
            >
              {ko.ontology.graph.focusOff}
            </button>
          ) : (
            <button
              type="button"
              className="link"
              disabled={selection?.kind !== "type"}
              onClick={() => selection?.kind === "type" && startFocus(selection.name, 1)}
            >
              {ko.ontology.graph.focus}
            </button>
          )}
          {saveLayout.isPending && <span className="muted">{ko.ontology.graph.saving}</span>}
        </div>
        <p className="muted">{editable ? ko.ontology.graph.hint : ko.ontology.graph.readonlyHint}</p>
        {diff.isError && <ErrorBox error={diff.error} />}
        {saveLayout.isError && <ErrorBox error={saveLayout.error} />}

        <div className="schema-body">
          <div className="schema-canvas">
            {elements.types.length === 0 && <p className="schema-empty muted">{ko.ontology.graph.empty}</p>}
            <ReactFlow<TypeNode, Edge<LinkEdgeData>>
              nodes={nodes}
              edges={edges}
              nodeTypes={NODE_TYPES}
              onNodesChange={onNodesChange}
              onNodeDragStop={(_, node) => {
                placed.current.set(node.id, node.position);
                scheduleSave();
              }}
              onNodeClick={(_, node) => setSelection({ kind: "type", name: node.id })}
              onEdgeClick={(_, edge) => setSelection({ kind: "link", name: edge.id })}
              onPaneClick={(e) => {
                if (e.detail === 2 && editable) {
                  setSelection({ kind: "newType", at: flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }) });
                }
              }}
              onConnect={onConnect}
              onBeforeDelete={async ({ nodes: n, edges: e }) => {
                if (n[0]) setDeleting({ kind: "object_type", name: n[0].id });
                else if (e[0]) setDeleting({ kind: "link_type", name: e[0].id });
                return false; // 지우기는 영향 확인 창을 거친다.
              }}
              deleteKeyCode={editable ? ["Delete", "Backspace"] : null}
              zoomOnDoubleClick={false}
              nodesConnectable={editable}
              minZoom={0.2}
              maxZoom={1.5}
            >
              <Background />
              <Controls showInteractive={false} />
              <MiniMap pannable zoomable />
            </ReactFlow>
          </div>

          {selection && (
            <aside className="schema-panel card stack">
              <div className="row">
                <h3>
                  {selection.kind === "newType"
                    ? ko.ontology.graph.newType
                    : selection.kind === "newLink"
                      ? ko.ontology.graph.newLink
                      : selection.kind === "link"
                        ? ko.ontology.graph.linkPanel
                        : (selectedType?.display_name ?? selection.name)}
                </h3>
                <span className="spacer" />
                <button type="button" className="link" onClick={() => setSelection(null)}>
                  {ko.ontology.graph.close}
                </button>
              </div>
              {mutationError && <ErrorBox error={mutationError} />}
              {selectedGhost && <p className="notice">{ko.ontology.graph.ghost}</p>}

              {selection.kind === "newType" && (
                <ObjectTypeForm
                  pending={addType.isPending}
                  onSubmit={(t) =>
                    addType.mutate(t, {
                      onSuccess: () => {
                        placed.current.set(t.api_name, selection.at);
                        scheduleSave();
                        setSelection({ kind: "type", name: t.api_name });
                      },
                    })
                  }
                />
              )}

              {selection.kind === "newLink" && (
                <LinkTypeForm
                  key={`${selection.from}>${selection.to}`}
                  typeNames={typeNames}
                  initial={{ from_type: selection.from, to_type: selection.to }}
                  fkChoices={fkChoices}
                  pending={addLink.isPending}
                  onSubmit={(l) =>
                    addLink.mutate(l, { onSuccess: () => setSelection({ kind: "link", name: l.api_name }) })
                  }
                />
              )}

              {selectedType && (
                <>
                  <code>{selectedType.api_name}</code>
                  {elements.types
                    .find((t) => t.name === selectedType.api_name)
                    ?.issues.map((i) => (
                      <p key={i.path + i.message} className="warn-text">
                        <code>{i.path}</code> {i.message}
                      </p>
                    ))}
                  {editable ? (
                    <details open>
                      <summary>{ko.ontology.graph.typeHead}</summary>
                      <ObjectTypeHeadForm
                        key={`${selectedType.id}:${selectedType.display_name}:${selectedType.deprecated}`}
                        initial={{
                          display_name: selectedType.display_name,
                          description: selectedType.description,
                          pk_property: selectedType.pk_property,
                          title_property: selectedType.title_property,
                          deprecated: selectedType.deprecated,
                        }}
                        propertyNames={selectedType.properties.map((p) => p.api_name)}
                        pending={patchType.isPending}
                        onSubmit={(body) => patchType.mutate({ id: selectedType.id, body })}
                      />
                    </details>
                  ) : (
                    selectedType.description && <p className="muted">{selectedType.description}</p>
                  )}
                  <ObjectTypeCard versionId={v.id} t={selectedType} editable={editable} compact />
                  {editable && (
                    <button
                      type="button"
                      className="link danger"
                      onClick={() => setDeleting({ kind: "object_type", name: selectedType.api_name })}
                    >
                      {ko.ontology.graph.delete}
                    </button>
                  )}
                </>
              )}

              {selectedLink && (
                <>
                  {elements.links
                    .find((l) => l.name === selectedLink.api_name)
                    ?.issues.map((i) => (
                      <p key={i.path + i.message} className="warn-text">
                        <code>{i.path}</code> {i.message}
                      </p>
                    ))}
                  {editable ? (
                    <LinkTypeForm
                      key={`${selectedLink.id}:${selectedLink.deprecated}`}
                      typeNames={typeNames}
                      initial={linkDef(selectedLink)}
                      lockName
                      fkChoices={fkChoices}
                      pending={patchLink.isPending}
                      onSubmit={(body) => patchLink.mutate({ id: selectedLink.id, body })}
                    />
                  ) : (
                    <p>
                      <code>{selectedLink.api_name}</code> {selectedLink.from_type} → {selectedLink.to_type} (
                      {selectedLink.cardinality})
                    </p>
                  )}
                  {editable && (
                    <button
                      type="button"
                      className="link danger"
                      onClick={() => setDeleting({ kind: "link_type", name: selectedLink.api_name })}
                    >
                      {ko.ontology.graph.delete}
                    </button>
                  )}
                </>
              )}
            </aside>
          )}
        </div>

        {deleting && (
          <DeleteDialog
            v={v}
            target={deleting}
            inParent={inParent(deleting)}
            onClose={() => {
              setDeleting(null);
              setSelection(null);
            }}
          />
        )}
      </div>
    </NodeActions.Provider>
  );
}
