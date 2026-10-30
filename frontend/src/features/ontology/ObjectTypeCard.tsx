import { useState } from "react";

import { api } from "../../api/client";
import type { ObjectTypeOut, PropertyDef, PropertyOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { PropertyForm } from "./forms";
import { useVersionMutation, V } from "./queries";

/** 객체 타입 하나 — 속성 표와 속성 추가·수정·삭제. 목록 화면과 그래프 옆 패널이 함께 쓴다. */
export function ObjectTypeCard({
  versionId,
  t,
  editable,
  compact = false,
}: {
  versionId: number;
  t: ObjectTypeOut;
  editable: boolean;
  /** 그래프 옆 패널 — 머리(이름·삭제)는 패널이 따로 그린다. */
  compact?: boolean;
}) {
  const base = `${V}/${versionId}/object-types/${t.id}`;
  const [editing, setEditing] = useState<number | null>(null);
  const addProp = useVersionMutation(versionId, (p: PropertyDef) => api.post(`${base}/properties`, p));
  const editProp = useVersionMutation(versionId, (a: { id: number; p: PropertyDef }) =>
    api.patch(`${base}/properties/${a.id}`, a.p),
  );
  const delProp = useVersionMutation(versionId, (propId: number) => api.del(`${base}/properties/${propId}`));
  const delType = useVersionMutation(versionId, () => api.del(base));
  const error = addProp.error ?? editProp.error ?? delProp.error ?? delType.error;

  const toDef = (p: PropertyOut): PropertyDef => {
    const def: PropertyDef & { id?: number } = { ...p };
    delete def.id;
    return def;
  };

  return (
    <article className={compact ? "stack" : "card stack"}>
      {!compact && (
        <div className="row">
          <h3>
            {t.display_name} <code>{t.api_name}</code>
          </h3>
          {t.deprecated && <span className="badge removed">{ko.ontology.deprecated}</span>}
          <span className="muted">
            {ko.ontology.pk} <code>{t.pk_property}</code> · {ko.ontology.title} <code>{t.title_property}</code>
          </span>
          <span className="spacer" />
          {editable && (
            <button
              type="button"
              className="link danger"
              onClick={() => window.confirm(ko.common.confirmDelete) && delType.mutate(undefined)}
            >
              {ko.common.delete}
            </button>
          )}
        </div>
      )}
      {!compact && t.description && <p className="muted">{t.description}</p>}
      <table>
        <thead>
          <tr>
            <th>{ko.ontology.apiName}</th>
            <th>{ko.ontology.displayName}</th>
            <th>{ko.ontology.dataType}</th>
            <th>{ko.ontology.nullable}</th>
            <th>{ko.ontology.indexed}</th>
            <th>{ko.ontology.piiKind}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {t.properties.map((p) =>
            editing === p.id ? (
              <tr key={p.id}>
                <td colSpan={7}>
                  <PropertyForm
                    initial={toDef(p)}
                    pending={editProp.isPending}
                    onCancel={() => setEditing(null)}
                    onSubmit={(def) => editProp.mutate({ id: p.id, p: def }, { onSuccess: () => setEditing(null) })}
                  />
                </td>
              </tr>
            ) : (
              <tr key={p.id}>
                <td>
                  <code>{p.api_name}</code>
                </td>
                <td>{p.display_name}</td>
                <td>
                  {p.data_type}
                  {p.enum_values ? ` (${p.enum_values.join(", ")})` : ""}
                </td>
                <td>{p.nullable ? "예" : "아니오"}</td>
                <td>{p.indexed ? "예" : ""}</td>
                <td>{p.pii_kind ?? ""}</td>
                <td className="actions">
                  {editable && (
                    <>
                      <button type="button" className="link" onClick={() => setEditing(p.id)}>
                        수정
                      </button>
                      <button
                        type="button"
                        className="link danger"
                        onClick={() => window.confirm(ko.common.confirmDelete) && delProp.mutate(p.id)}
                      >
                        {ko.common.delete}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
      {error && <ErrorBox error={error} />}
      {editable && (
        <details>
          <summary>{ko.ontology.addProperty}</summary>
          <PropertyForm key={t.properties.length} pending={addProp.isPending} onSubmit={(p) => addProp.mutate(p)} />
        </details>
      )}
    </article>
  );
}
