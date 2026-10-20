import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api } from "../../api/client";
import type { ActionCandidateOut, ActionTypeDef, ActionTypeOut, ObjectTypeOut, VersionDetail } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { useVersionMutation, V } from "./queries";

const t = ko.ontology;
type Param = NonNullable<ActionTypeDef["parameters"]>[number];
type Cond = NonNullable<ActionTypeDef["preconditions"]>[number];
type Change = NonNullable<ActionTypeDef["changes"]>[number];
type Notify = NonNullable<ActionTypeDef["notify"]>[number];
const PARAM_TYPES: Param["data_type"][] = ["string", "int", "decimal", "bool", "date", "datetime", "enum"];
const OPS: Cond["op"][] = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "is_null", "not_null"];
const CHANNELS: Notify["channel"][] = ["app", "email", "sms"];

const csv = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

/** Action 정의 목록과 편집(draft 만). 교차 검사는 서버의 '게시 전 검사'가 정본이다. */
export function ActionTypesSection({ v, editable }: { v: VersionDetail; editable: boolean }) {
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const add = useVersionMutation(v.id, (body: ActionTypeDef) => api.post(`${V}/${v.id}/action-types`, body));
  const save = useVersionMutation(v.id, ({ id, body }: { id: number; body: ActionTypeDef }) =>
    api.put(`${V}/${v.id}/action-types/${id}`, body),
  );
  const del = useVersionMutation(v.id, (id: number) => api.del(`${V}/${v.id}/action-types/${id}`));

  return (
    <>
      <h2>{t.actionTypes}</h2>
      <p className="muted">{t.actionTypesHint}</p>
      {v.action_types.length === 0 && <p className="muted">{t.noActionTypes}</p>}
      {v.action_types.map((a) =>
        editing === a.id ? (
          <div key={a.id} className="card">
            <ActionForm
              types={v.object_types}
              initial={a}
              pending={save.isPending}
              onCancel={() => setEditing(null)}
              onSubmit={(body) => save.mutate({ id: a.id, body }, { onSuccess: () => setEditing(null) })}
            />
            {save.isError && <ErrorBox error={save.error} />}
          </div>
        ) : (
          <ActionSummary
            key={a.id}
            a={a}
            editable={editable}
            onEdit={() => setEditing(a.id)}
            onDelete={() => window.confirm(ko.common.confirmDelete) && del.mutate(a.id)}
          />
        ),
      )}
      {del.isError && <ErrorBox error={del.error} />}
      {editable && editing !== "new" && (
        <button type="button" className="link" onClick={() => setEditing("new")}>
          {t.addActionType}
        </button>
      )}
      {editable && editing === "new" && (
        <div className="card stack">
          <ActionForm
            types={v.object_types}
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

function ActionSummary({
  a,
  editable,
  onEdit,
  onDelete,
}: {
  a: ActionTypeOut;
  editable: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="card">
      <div className="row">
        <strong>{a.display_name}</strong> <code>{a.api_name}</code>
        <span className="muted">→ {a.target_type}</span>
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
      {a.description && <p className="muted">{a.description}</p>}
      <ul className="muted">
        <li>
          {t.changes}:{" "}
          {(a.changes ?? []).map((c) => `${c.property} ← ${c.param ? `{${c.param}}` : String(c.value)}`).join(", ") || "—"}
        </li>
        <li>
          {t.notify}:{" "}
          {(a.notify ?? [])
            .map((n) => `${t.channel[n.channel]}(${[...(n.to_roles ?? []), n.to_property].filter(Boolean).join(", ")})`)
            .join(", ") || "—"}
        </li>
        <li>
          {t.approvers}: {(a.approval.roles ?? []).join(", ") || "—"}
          {a.approval.allow_self && ` · ${t.allowSelf}`}
        </li>
      </ul>
    </div>
  );
}

function blank(): ActionTypeDef {
  return {
    api_name: "",
    display_name: "",
    target_type: "",
    requester_roles: [],
    parameters: [],
    preconditions: [],
    changes: [],
    notify: [],
    approval: { roles: ["modeler"], allow_self: false },
    compensation: null,
  };
}

function ActionForm({
  types,
  initial,
  pending,
  onSubmit,
  onCancel,
}: {
  types: ObjectTypeOut[];
  initial?: ActionTypeOut;
  pending: boolean;
  onSubmit: (a: ActionTypeDef) => void;
  onCancel: () => void;
}) {
  const [a, setA] = useState<ActionTypeDef>(() => {
    if (!initial) return blank();
    // 응답의 id 는 정의가 아니다 — 보내지 않는다.
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { id, ...def } = initial;
    return def;
  });
  const [requesters, setRequesters] = useState((a.requester_roles ?? []).join(", "));
  const [approvers, setApprovers] = useState((a.approval.roles ?? []).join(", "));
  const target = types.find((x) => x.api_name === a.target_type);
  const props = target?.properties ?? [];
  const params = a.parameters ?? [];
  const conds = a.preconditions ?? [];
  const changes = a.changes ?? [];
  const notify = a.notify ?? [];

  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({
      ...a,
      description: a.description || null,
      compensation: a.compensation?.trim() ? a.compensation : null,
      requester_roles: csv(requesters),
      approval: { ...a.approval, roles: csv(approvers) },
    });
  };
  const setAt = <T,>(list: T[], i: number, item: T) => list.map((x, j) => (j === i ? item : x));
  const without = <T,>(list: T[], i: number) => list.filter((_, j) => j !== i);

  return (
    <form onSubmit={submit} className="stack">
      {!initial && <CandidatePicker onPick={(c) => setA({ ...a, display_name: c.name, description: c.description })} />}
      <div className="grid-form">
        <label>
          {t.apiName}
          <input
            value={a.api_name}
            onChange={(e) => setA({ ...a, api_name: e.target.value })}
            placeholder="adjust_risk_grade"
            disabled={initial !== undefined}
            required
          />
        </label>
        <label>
          {t.displayName}
          <input value={a.display_name} onChange={(e) => setA({ ...a, display_name: e.target.value })} required />
        </label>
        <label>
          {t.target}
          <select value={a.target_type} onChange={(e) => setA({ ...a, target_type: e.target.value })} required>
            <option value="">—</option>
            {types.map((x) => (
              <option key={x.api_name} value={x.api_name}>
                {x.display_name} ({x.api_name})
              </option>
            ))}
          </select>
        </label>
        <label>
          {t.description}
          <input value={a.description ?? ""} onChange={(e) => setA({ ...a, description: e.target.value })} />
        </label>
        <label>
          {t.requesterRoles}
          <input value={requesters} onChange={(e) => setRequesters(e.target.value)} placeholder="user" />
        </label>
        <label>
          {t.approvalRoles}
          <input value={approvers} onChange={(e) => setApprovers(e.target.value)} placeholder="modeler" />
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={a.approval.allow_self}
            onChange={(e) => setA({ ...a, approval: { ...a.approval, allow_self: e.target.checked } })}
          />
          {t.allowSelf}
        </label>
      </div>

      <fieldset>
        <legend>{t.parameters}</legend>
        {params.map((p, i) => (
          <div key={i} className="row">
            <input
              value={p.api_name}
              placeholder={t.apiName}
              onChange={(e) => setA({ ...a, parameters: setAt(params, i, { ...p, api_name: e.target.value }) })}
              required
            />
            <input
              value={p.display_name}
              placeholder={t.displayName}
              onChange={(e) => setA({ ...a, parameters: setAt(params, i, { ...p, display_name: e.target.value }) })}
              required
            />
            <select
              value={p.data_type}
              onChange={(e) => {
                const data_type = e.target.value as Param["data_type"];
                setA({
                  ...a,
                  parameters: setAt(params, i, { ...p, data_type, enum_values: data_type === "enum" ? [] : null }),
                });
              }}
            >
              {PARAM_TYPES.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
            {p.data_type === "enum" && (
              <input
                value={(p.enum_values ?? []).join(", ")}
                placeholder={t.enumValues}
                onChange={(e) => setA({ ...a, parameters: setAt(params, i, { ...p, enum_values: csv(e.target.value) }) })}
              />
            )}
            <label className="check">
              <input
                type="checkbox"
                checked={p.required}
                onChange={(e) => setA({ ...a, parameters: setAt(params, i, { ...p, required: e.target.checked }) })}
              />
              {t.required}
            </label>
            <button type="button" className="link danger" onClick={() => setA({ ...a, parameters: without(params, i) })}>
              {t.removeRow}
            </button>
          </div>
        ))}
        <button
          type="button"
          className="link"
          onClick={() =>
            setA({ ...a, parameters: [...params, { api_name: "", display_name: "", data_type: "string", required: true }] })
          }
        >
          {t.addRow}
        </button>
      </fieldset>

      <fieldset>
        <legend>{t.preconditions}</legend>
        {conds.map((c, i) => (
          <div key={i} className="row">
            <PropSelect props={props} value={c.property} onChange={(property) => setA({ ...a, preconditions: setAt(conds, i, { ...c, property }) })} />
            <select
              value={c.op}
              onChange={(e) => setA({ ...a, preconditions: setAt(conds, i, { ...c, op: e.target.value as Cond["op"] }) })}
            >
              {OPS.map((x) => (
                <option key={x} value={x}>
                  {t.ops[x]}
                </option>
              ))}
            </select>
            {c.op !== "is_null" && c.op !== "not_null" && (
              <input
                value={Array.isArray(c.value) ? c.value.join(", ") : String(c.value ?? "")}
                placeholder={t.value}
                onChange={(e) =>
                  setA({
                    ...a,
                    preconditions: setAt(conds, i, { ...c, value: c.op === "in" ? csv(e.target.value) : e.target.value }),
                  })
                }
              />
            )}
            <button type="button" className="link danger" onClick={() => setA({ ...a, preconditions: without(conds, i) })}>
              {t.removeRow}
            </button>
          </div>
        ))}
        <button
          type="button"
          className="link"
          onClick={() => setA({ ...a, preconditions: [...conds, { property: "", op: "eq", value: "" }] })}
        >
          {t.addRow}
        </button>
      </fieldset>

      <fieldset>
        <legend>{t.changes}</legend>
        {changes.map((c, i) => (
          <div key={i} className="row">
            <PropSelect props={props} value={c.property} onChange={(property) => setA({ ...a, changes: setAt(changes, i, { ...c, property }) })} />
            <span>←</span>
            <select
              value={c.param ? `p:${c.param}` : "const"}
              onChange={(e) => {
                const v = e.target.value;
                const next: Change = v === "const" ? { property: c.property, value: "" } : { property: c.property, param: v.slice(2) };
                setA({ ...a, changes: setAt(changes, i, next) });
              }}
            >
              <option value="const">{t.constant}</option>
              {params.map((p) => (
                <option key={p.api_name} value={`p:${p.api_name}`}>
                  {t.fromParam} {p.api_name}
                </option>
              ))}
            </select>
            {!c.param && (
              <input
                value={String(c.value ?? "")}
                placeholder={t.value}
                onChange={(e) => setA({ ...a, changes: setAt(changes, i, { property: c.property, value: e.target.value }) })}
                required
              />
            )}
            <button type="button" className="link danger" onClick={() => setA({ ...a, changes: without(changes, i) })}>
              {t.removeRow}
            </button>
          </div>
        ))}
        <button type="button" className="link" onClick={() => setA({ ...a, changes: [...changes, { property: "", value: "" }] })}>
          {t.addRow}
        </button>
      </fieldset>

      <fieldset>
        <legend>{t.notify}</legend>
        {notify.map((n, i) => (
          <div key={i} className="stack">
            <div className="row">
              <select
                value={n.channel}
                onChange={(e) =>
                  setA({
                    ...a,
                    notify: setAt(notify, i, { ...n, channel: e.target.value as Notify["channel"], to_property: null }),
                  })
                }
              >
                {CHANNELS.map((x) => (
                  <option key={x} value={x}>
                    {t.channel[x]}
                  </option>
                ))}
              </select>
              <input
                value={(n.to_roles ?? []).join(", ")}
                placeholder={t.toRoles}
                onChange={(e) => setA({ ...a, notify: setAt(notify, i, { ...n, to_roles: csv(e.target.value) }) })}
              />
              {n.channel !== "app" && (
                <select
                  value={n.to_property ?? ""}
                  title={t.toProperty}
                  onChange={(e) => setA({ ...a, notify: setAt(notify, i, { ...n, to_property: e.target.value || null }) })}
                >
                  <option value="">{t.toProperty} —</option>
                  {props
                    .filter((p) => p.pii_kind === (n.channel === "email" ? "email" : "phone"))
                    .map((p) => (
                      <option key={p.api_name} value={p.api_name}>
                        {p.display_name}
                      </option>
                    ))}
                </select>
              )}
              <button type="button" className="link danger" onClick={() => setA({ ...a, notify: without(notify, i) })}>
                {t.removeRow}
              </button>
            </div>
            <input
              value={n.template}
              placeholder={t.template}
              onChange={(e) => setA({ ...a, notify: setAt(notify, i, { ...n, template: e.target.value }) })}
              required
            />
          </div>
        ))}
        <button
          type="button"
          className="link"
          onClick={() => setA({ ...a, notify: [...notify, { channel: "app", to_roles: [], template: "" }] })}
        >
          {t.addRow}
        </button>
      </fieldset>

      {notify.some((n) => n.channel !== "app") && (
        <label>
          {t.compensation}
          <input value={a.compensation ?? ""} onChange={(e) => setA({ ...a, compensation: e.target.value })} />
        </label>
      )}

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

function PropSelect({
  props,
  value,
  onChange,
}: {
  props: ObjectTypeOut["properties"];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} required>
      <option value="">—</option>
      {props.map((p) => (
        <option key={p.api_name} value={p.api_name}>
          {p.display_name} ({p.api_name})
        </option>
      ))}
    </select>
  );
}

/** 워크숍에서 확정한 Action 후보로 이름·설명을 채운다(나머지는 모델러가 정한다). */
function CandidatePicker({ onPick }: { onPick: (c: ActionCandidateOut) => void }) {
  const list = useQuery({
    queryKey: ["workshop", "action-candidates"],
    queryFn: () => api.get<ActionCandidateOut[]>("/workshop/action-candidates"),
  });
  if (!list.data) return null;
  return (
    <details>
      <summary>{t.fromCandidate}</summary>
      {list.data.length === 0 && <p className="muted">{t.candidateNone}</p>}
      <ul>
        {list.data.map((c) => (
          <li key={c.element_id}>
            <strong>{c.name}</strong> <span className="muted">{c.session_title ?? `#${c.session_id}`}</span>
            {c.quotes[0] && <div className="muted">“{c.quotes[0]}”</div>}
            <button type="button" className="link" onClick={() => onPick(c)}>
              {t.pick}
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}
