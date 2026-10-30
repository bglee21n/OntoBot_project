import { type FormEvent, useState } from "react";

import { api } from "../../api/client";
import type { FunctionDef, FunctionOut, VersionDetail } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { useVersionMutation, V } from "./queries";

const t = ko.ontology;

/** 새 Function 의 뼈대 — 첫 객체 타입의 건수를 그대로 돌려준다. 고쳐서 쓴다. */
function blank(v: VersionDetail): FunctionDef {
  const target = v.object_types[0]?.api_name ?? "";
  return {
    api_name: "",
    display_name: "",
    description: null,
    params: [],
    queries: [{ api_name: "total", display_name: "건수", target, filters: [], traverse: [], aggregate: { fn: "count", group_by: [] } }],
    body: { op: "query", name: "total", args: [] },
    result_label: "",
    result_type: "decimal",
    decimals: 2,
  };
}

/** Function 정의 목록과 편집(draft 만). 식 트리는 JSON 으로 고친다 — 형식·교차 검사는 서버가 정본이다. */
export function FunctionsSection({ v, editable }: { v: VersionDetail; editable: boolean }) {
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const add = useVersionMutation(v.id, (body: FunctionDef) => api.post(`${V}/${v.id}/functions`, body));
  const save = useVersionMutation(v.id, ({ id, body }: { id: number; body: FunctionDef }) =>
    api.put(`${V}/${v.id}/functions/${id}`, body),
  );
  const del = useVersionMutation(v.id, (id: number) => api.del(`${V}/${v.id}/functions/${id}`));

  return (
    <>
      <h2>{t.functions}</h2>
      <p className="muted">{t.functionsHint}</p>
      {v.functions.length === 0 && <p className="muted">{t.noFunctions}</p>}
      {v.functions.map((f) =>
        editing === f.id ? (
          <div key={f.id} className="card">
            <FunctionForm
              initial={strip(f)}
              pending={save.isPending}
              onCancel={() => setEditing(null)}
              onSubmit={(body) => save.mutate({ id: f.id, body }, { onSuccess: () => setEditing(null) })}
            />
            {save.isError && <ErrorBox error={save.error} />}
          </div>
        ) : (
          <FunctionSummary
            key={f.id}
            f={f}
            editable={editable}
            onEdit={() => setEditing(f.id)}
            onDelete={() => window.confirm(ko.common.confirmDelete) && del.mutate(f.id)}
          />
        ),
      )}
      {del.isError && <ErrorBox error={del.error} />}
      {editable && editing !== "new" && (
        <button type="button" className="link" onClick={() => setEditing("new")}>
          {t.addFunction}
        </button>
      )}
      {editable && editing === "new" && (
        <div className="card stack">
          <FunctionForm
            initial={blank(v)}
            pending={add.isPending}
            onCancel={() => setEditing(null)}
            onSubmit={(body) => add.mutate(body, { onSuccess: () => setEditing(null) })}
          />
          {add.isError && <ErrorBox error={add.error} />}
        </div>
      )}
    </>
  );
}

function strip(f: FunctionOut): FunctionDef {
  const def: FunctionDef & { id?: number } = { ...f };
  delete def.id;
  return def;
}

function FunctionSummary({
  f,
  editable,
  onEdit,
  onDelete,
}: {
  f: FunctionOut;
  editable: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const groups = f.queries.find((q) => (q.aggregate.group_by ?? []).length > 0)?.aggregate.group_by ?? [];
  return (
    <div className="card">
      <div className="row">
        <strong>{f.display_name}</strong> <code>{f.api_name}</code>
        <span className="spacer" />
        {editable && (
          <>
            <button type="button" className="link" onClick={onEdit}>
              {t.edit}
            </button>
            <button type="button" className="link danger" onClick={onDelete}>
              {ko.common.delete}
            </button>
          </>
        )}
      </div>
      {f.description && <p className="muted">{f.description}</p>}
      <ul className="muted">
        <li>
          {t.fnParams}:{" "}
          {(f.params ?? [])
            .map((p) => `${p.display_name}(${p.data_type}${p.required ? "" : `, ${t.defaultValue(p.default)}`})`)
            .join(", ") || "—"}
        </li>
        <li>
          {t.fnQueries}: {f.queries.map((q) => `${q.display_name} — ${q.aggregate.fn}(${q.target})`).join(", ")}
        </li>
        {groups.length > 0 && (
          <li>
            {t.fnGroupBy}: {groups.join(", ")}
          </li>
        )}
        <li>
          {t.fnResult}: {f.result_label}
        </li>
      </ul>
    </div>
  );
}

function FunctionForm({
  initial,
  pending,
  onCancel,
  onSubmit,
}: {
  initial: FunctionDef;
  pending: boolean;
  onCancel: () => void;
  onSubmit: (body: FunctionDef) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(initial, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    try {
      setParseError(null);
      onSubmit(JSON.parse(text) as FunctionDef);
    } catch (err) {
      setParseError(t.jsonInvalid(err instanceof Error ? err.message : String(err)));
    }
  };

  return (
    <form className="stack" onSubmit={submit}>
      <label className="stack">
        <span className="muted">{t.functionJson}</span>
        <textarea rows={24} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      </label>
      {parseError && <p className="error">{parseError}</p>}
      <div className="row">
        <button type="submit" disabled={pending}>
          {t.save}
        </button>
        <button type="button" className="link" onClick={onCancel}>
          {t.cancel}
        </button>
      </div>
    </form>
  );
}
