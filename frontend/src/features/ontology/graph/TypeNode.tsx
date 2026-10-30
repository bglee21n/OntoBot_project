import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import { createContext, useContext } from "react";

import { ko } from "../../../i18n/ko";
import type { TypeNodeData } from "./model";

export type TypeNode = Node<TypeNodeData, "type">;

/** 노드 안의 펼치기·접기 단추가 부르는 동작(포커스 중일 때만 보인다). */
export const NodeActions = createContext<{ expand: (name: string) => void; collapse: (name: string) => void } | null>(
  null,
);

export function TypeNodeView({ data, selected }: NodeProps<TypeNode>) {
  const actions = useContext(NodeActions);
  const classes = [
    "schema-node",
    data.change ? `change-${data.change}` : "",
    data.deprecated ? "deprecated" : "",
    data.ghost ? "ghost" : "",
    data.dimmed ? "dimmed" : "",
    data.issues.length ? "has-issues" : "",
    selected ? "selected" : "",
  ];
  return (
    <div className={classes.filter(Boolean).join(" ")} title={data.ghost ? ko.ontology.graph.ghost : undefined}>
      <Handle type="target" position={Position.Left} />
      <div className="schema-node-head">
        <strong>{data.displayName}</strong>
        {data.change && <span className={`badge ${data.change}`}>{ko.ontology.changeKind[data.change]}</span>}
        {data.deprecated && <span className="badge removed">{ko.ontology.deprecated}</span>}
      </div>
      <code>{data.name}</code>
      <div className="schema-node-meta muted">
        {ko.ontology.pk} <code>{data.pk}</code> · {ko.ontology.graph.properties(data.propertyCount)}
        {data.hasPii && <span className="badge flag">{ko.ontology.graph.pii}</span>}
      </div>
      {data.issues.length > 0 && <div className="schema-node-issues">{ko.ontology.graph.issues(data.issues.length)}</div>}
      {data.actions.length + data.functions.length > 0 && (
        <div className="schema-node-actions">
          {data.actions.map((a) => (
            <span key={`a:${a}`} className="badge">
              {a}
            </span>
          ))}
          {data.functions.map((f) => (
            <span key={`f:${f}`} className="badge fn" title={ko.ontology.functions}>
              ƒ {f}
            </span>
          ))}
        </div>
      )}
      {actions && (data.hiddenNeighbors > 0 || data.collapsible) && (
        <div className="schema-node-expand nodrag">
          {data.collapsible && (
            <button type="button" className="link" title={ko.ontology.graph.collapse} onClick={() => actions.collapse(data.name)}>
              −
            </button>
          )}
          {data.hiddenNeighbors > 0 && (
            <button type="button" className="link" title={ko.ontology.graph.expand} onClick={() => actions.expand(data.name)}>
              +{data.hiddenNeighbors}
            </button>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
