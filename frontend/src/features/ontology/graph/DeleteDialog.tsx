import { useState } from "react";

import { api } from "../../../api/client";
import type { VersionDetail } from "../../../api/types";
import { ErrorBox } from "../../../components/ErrorBox";
import { ko } from "../../../i18n/ko";
import { useReferences, useVersionMutation, V } from "../queries";

export interface DeleteTarget {
  kind: "object_type" | "link_type";
  name: string;
}

/**
 * 삭제 확인 — 먼저 이 요소를 쓰는 곳(영향)을 보여 준다. 게시본에 이미 있던 요소면 폐기를 권한다.
 * 연쇄 삭제는 하지 않는다: 함께 지울 링크·Action 은 사용자가 고른 것만 차례로 지운다.
 */
export function DeleteDialog({
  v,
  target,
  inParent,
  onClose,
}: {
  v: VersionDetail;
  target: DeleteTarget;
  inParent: boolean;
  onClose: () => void;
}) {
  const refs = useReferences(v.id, target);
  const [also, setAlso] = useState<Set<string>>(new Set());
  const t = ko.ontology.impact;

  const run = useVersionMutation(v.id, async (mode: "deprecate" | "delete") => {
    const type = v.object_types.find((x) => x.api_name === target.name);
    const link = v.link_types.find((x) => x.api_name === target.name);
    if (mode === "deprecate") {
      if (target.kind === "object_type" && type) {
        await api.patch(`${V}/${v.id}/object-types/${type.id}`, { deprecated: true });
      } else if (link) {
        const { id, ...def } = link;
        await api.patch(`${V}/${v.id}/link-types/${id}`, { ...def, deprecated: true });
      }
      return;
    }
    for (const path of also) {
      const [head, name] = path.split(".");
      if (head === "link_types") {
        const l = v.link_types.find((x) => x.api_name === name);
        if (l) await api.del(`${V}/${v.id}/link-types/${l.id}`);
      } else if (head === "action_types") {
        const a = v.action_types.find((x) => x.api_name === name);
        if (a) await api.del(`${V}/${v.id}/action-types/${a.id}`);
      }
    }
    if (target.kind === "object_type" && type) await api.del(`${V}/${v.id}/object-types/${type.id}`);
    else if (link) await api.del(`${V}/${v.id}/link-types/${link.id}`);
  });

  const done = (mode: "deprecate" | "delete") => run.mutate(mode, { onSuccess: onClose });
  const data = refs.data;
  const nothing =
    data && !data.in_document.length && !data.mappings.length && !data.golden_items.length && !data.has_data;
  const toggle = (path: string) =>
    setAlso((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  return (
    <div className="dialog-backdrop" role="presentation" onClick={onClose}>
      <div className="dialog card stack" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h3>{t.title(target.name)}</h3>
        {refs.isPending && <p>{t.loading}</p>}
        {refs.isError && <ErrorBox error={refs.error} />}
        {data && (
          <>
            {nothing && <p>{t.none}</p>}
            {data.in_document.length > 0 && (
              <div>
                <strong>{t.inDocument}</strong>
                <ul>
                  {data.in_document.map((r) => (
                    <li key={r.path + r.message}>
                      {r.path.startsWith("object_types.") ? (
                        r.message
                      ) : (
                        <label className="check">
                          <input type="checkbox" checked={also.has(r.path)} onChange={() => toggle(r.path)} />
                          {r.message} — {t.alsoDelete}
                        </label>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {data.mappings.length > 0 && (
              <div>
                <strong>{t.mappings}</strong>
                <ul>
                  {data.mappings.map((m) => (
                    <li key={m.id}>
                      {m.data_source} → <code>{m.object_type}</code> (#{m.id})
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {data.golden_items.length > 0 && <p>{t.golden(data.golden_items.length)}</p>}
            {data.has_data && <p className="warn-text">{t.hasData}</p>}
          </>
        )}
        {inParent && <p className="notice">{t.published}</p>}
        {run.isError && <ErrorBox error={run.error} />}
        <div className="row">
          {inParent && (
            <button type="button" disabled={run.isPending} onClick={() => done("deprecate")}>
              {t.deprecate}
            </button>
          )}
          <button
            type="button"
            className={inParent ? "link danger" : "danger"}
            disabled={run.isPending || refs.isPending}
            onClick={() => done("delete")}
          >
            {inParent ? t.deleteAnyway : t.delete}
          </button>
          <span className="spacer" />
          <button type="button" className="link" onClick={onClose}>
            {t.cancel}
          </button>
        </div>
      </div>
    </div>
  );
}
