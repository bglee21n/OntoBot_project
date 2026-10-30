import { useState } from "react";

import { api } from "../../../api/client";
import type { ActionTypeDef, FunctionDef, VersionDetail } from "../../../api/types";
import { ErrorBox } from "../../../components/ErrorBox";
import { ko } from "../../../i18n/ko";
import { ActionForm } from "../ActionTypes";
import { blankFunction, FunctionForm, strip } from "../Functions";
import { useVersionMutation, V } from "../queries";

const t = ko.ontology;
type Editing = { kind: "action" | "function"; id: number | "new" } | null;

/** 이 Function 이 조회하는 타입(대상·탐색 끝) — 서버 ``FunctionDef.types()`` 와 같은 규칙. */
function functionTypes(f: VersionDetail["functions"][number]): Set<string> {
  return new Set(f.queries.flatMap((q) => [q.target, ...(q.traverse ?? []).map((tr) => tr.to)]));
}

/**
 * 스키마 그래프 옆 패널의 Action·Function — 고른 타입을 대상으로 하는 Action 과 그 타입을 조회하는 Function 을
 * 그래프에서 바로 만들고 고치고 지운다(목록 탭의 섹션과 같은 폼·API). 교차 검사는 게시 전 검사가 정본이다.
 */
export function TypeBehaviors({ v, typeName, editable }: { v: VersionDetail; typeName: string; editable: boolean }) {
  const [editing, setEditing] = useState<Editing>(null);
  const done = { onSuccess: () => setEditing(null) };
  const addAction = useVersionMutation(v.id, (body: ActionTypeDef) => api.post(`${V}/${v.id}/action-types`, body));
  const saveAction = useVersionMutation(v.id, ({ id, body }: { id: number; body: ActionTypeDef }) =>
    api.put(`${V}/${v.id}/action-types/${id}`, body),
  );
  const delAction = useVersionMutation(v.id, (id: number) => api.del(`${V}/${v.id}/action-types/${id}`));
  const addFn = useVersionMutation(v.id, (body: FunctionDef) => api.post(`${V}/${v.id}/functions`, body));
  const saveFn = useVersionMutation(v.id, ({ id, body }: { id: number; body: FunctionDef }) =>
    api.put(`${V}/${v.id}/functions/${id}`, body),
  );
  const delFn = useVersionMutation(v.id, (id: number) => api.del(`${V}/${v.id}/functions/${id}`));
  const error = [addAction, saveAction, delAction, addFn, saveFn, delFn].find((m) => m.isError)?.error;

  const actions = v.action_types.filter((a) => a.target_type === typeName);
  const functions = v.functions.filter((f) => functionTypes(f).has(typeName));
  const is = (kind: "action" | "function", id: number | "new") => editing?.kind === kind && editing.id === id;
  const confirmDelete = () => window.confirm(ko.common.confirmDelete);

  return (
    <div className="stack">
      {error && <ErrorBox error={error} />}
      <details open={actions.length > 0 || is("action", "new")}>
        <summary>
          {t.actionTypes} ({actions.length})
        </summary>
        {actions.map((a) =>
          is("action", a.id) ? (
            <ActionForm
              key={a.id}
              types={v.object_types}
              initial={a}
              pending={saveAction.isPending}
              onCancel={() => setEditing(null)}
              onSubmit={(body) => saveAction.mutate({ id: a.id, body }, done)}
            />
          ) : (
            <div key={a.id} className="row">
              <span>
                {a.display_name} <code>{a.api_name}</code>
              </span>
              <span className="spacer" />
              {editable && (
                <>
                  <button type="button" className="link" onClick={() => setEditing({ kind: "action", id: a.id })}>
                    {t.edit}
                  </button>
                  <button type="button" className="link danger" onClick={() => confirmDelete() && delAction.mutate(a.id)}>
                    {ko.common.delete}
                  </button>
                </>
              )}
            </div>
          ),
        )}
        {editable &&
          (is("action", "new") ? (
            <ActionForm
              types={v.object_types}
              presetTarget={typeName}
              pending={addAction.isPending}
              onCancel={() => setEditing(null)}
              onSubmit={(body) => addAction.mutate(body, done)}
            />
          ) : (
            <button type="button" className="link" onClick={() => setEditing({ kind: "action", id: "new" })}>
              {t.addActionType}
            </button>
          ))}
      </details>

      <details open={functions.length > 0 || is("function", "new")}>
        <summary>
          {t.functions} ({functions.length})
        </summary>
        {functions.map((f) =>
          is("function", f.id) ? (
            <FunctionForm
              key={f.id}
              initial={strip(f)}
              pending={saveFn.isPending}
              onCancel={() => setEditing(null)}
              onSubmit={(body) => saveFn.mutate({ id: f.id, body }, done)}
            />
          ) : (
            <div key={f.id} className="row">
              <span>
                {f.display_name} <code>{f.api_name}</code>
              </span>
              <span className="spacer" />
              {editable && (
                <>
                  <button type="button" className="link" onClick={() => setEditing({ kind: "function", id: f.id })}>
                    {t.edit}
                  </button>
                  <button type="button" className="link danger" onClick={() => confirmDelete() && delFn.mutate(f.id)}>
                    {ko.common.delete}
                  </button>
                </>
              )}
            </div>
          ),
        )}
        {editable &&
          (is("function", "new") ? (
            <FunctionForm
              initial={blankFunction(v, typeName)}
              pending={addFn.isPending}
              onCancel={() => setEditing(null)}
              onSubmit={(body) => addFn.mutate(body, done)}
            />
          ) : (
            <button type="button" className="link" onClick={() => setEditing({ kind: "function", id: "new" })}>
              {t.addFunction}
            </button>
          ))}
      </details>
    </div>
  );
}
