import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useId, useMemo, useState } from "react";

import { api } from "../../api/client";
import type { ActionRunOut, ActionTypeView, NotificationList } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.actions;
type Scope = "to_decide" | "mine" | "all";

const newRequestId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID().replace(/-/g, "")
    : `${Date.now()}${Math.random().toString(16).slice(2)}`;

export function useNotifications() {
  return useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get<NotificationList>("/notifications"),
    refetchInterval: 30_000,
  });
}

/** Action — 요청(미리보기) → 승인(실행) → 롤백, 그리고 알림함(PRD 5.3). */
export function ActionsPage() {
  const [scope, setScope] = useState<Scope>("to_decide");
  return (
    <section className="stack">
      <h1>{t.title}</h1>
      <p className="muted">{t.intro}</p>
      <RequestPanel />
      <nav className="row" role="tablist">
        {(Object.keys(t.tabs) as Scope[]).map((k) => (
          <button key={k} type="button" role="tab" aria-selected={scope === k} className={scope === k ? "" : "link"} onClick={() => setScope(k)}>
            {t.tabs[k]}
          </button>
        ))}
      </nav>
      <RunList scope={scope} />
      <NotificationsPanel />
    </section>
  );
}

/** 채팅의 Action 제안이 대조를 통과하지 못했을 때 폼을 채워 둘 값(서버가 확인한 것만). */
export interface RequestInitial {
  action: string;
  pk: string;
  params: Record<string, string>;
  candidates: { pk: string; title: string }[];
}

export function RequestPanel({ initial, title = t.request }: { initial?: RequestInitial; title?: string }) {
  const qc = useQueryClient();
  const types = useQuery({ queryKey: ["action-types"], queryFn: () => api.get<ActionTypeView[]>("/action-types") });
  const requestable = (types.data ?? []).filter((a) => a.can_request);
  const [name, setName] = useState(initial?.action ?? "");
  const [pk, setPk] = useState(initial?.pk ?? "");
  const [params, setParams] = useState<Record<string, string>>(initial?.params ?? {});
  const candidates = initial?.candidates ?? [];
  const listId = useId();
  // 같은 입력을 다시 보내면 같은 요청 ID — 서버가 한 번만 받는다. 입력이 바뀌면 새 ID.
  const [requestId, setRequestId] = useState(newRequestId);
  const action = requestable.find((a) => a.api_name === name);
  const preview = useMutation({
    mutationFn: () =>
      api.post<ActionRunOut>(`/action-types/${encodeURIComponent(name)}/preview`, {
        target_pk: pk,
        params: Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "")),
        request_id: requestId,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["action-runs"] }),
  });
  const touch = () => {
    setRequestId(newRequestId());
    preview.reset();
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    preview.mutate();
  };

  return (
    <div className="card stack">
      <strong>{title}</strong>
      {types.isError && <ErrorBox error={types.error} />}
      {types.data && requestable.length === 0 && <p className="muted">{t.noTypes}</p>}
      {requestable.length > 0 && (
        <form onSubmit={submit} className="grid-form">
          <label>
            {t.action}
            <select
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setParams({});
                touch();
              }}
              required
            >
              <option value="">—</option>
              {requestable.map((a) => (
                <option key={a.api_name} value={a.api_name}>
                  {a.display_name} ({a.target_display_name})
                </option>
              ))}
            </select>
          </label>
          <label>
            {t.targetPk}
            <input
              value={pk}
              onChange={(e) => {
                setPk(e.target.value);
                touch();
              }}
              list={candidates.length > 0 ? listId : undefined}
              required
            />
            {candidates.length > 0 && (
              <datalist id={listId}>
                {candidates.map((c) => (
                  <option key={c.pk} value={c.pk}>
                    {c.title}
                  </option>
                ))}
              </datalist>
            )}
          </label>
          {(action?.parameters ?? []).map((p) => (
            <label key={p.api_name}>
              {p.display_name}
              {p.required ? " *" : ""}
              {p.data_type === "enum" || p.data_type === "bool" ? (
                <select
                  value={params[p.api_name] ?? ""}
                  onChange={(e) => {
                    setParams({ ...params, [p.api_name]: e.target.value });
                    touch();
                  }}
                >
                  <option value="">—</option>
                  {(p.data_type === "bool" ? ["true", "false"] : (p.enum_values ?? [])).map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </select>
              ) : (
                <input
                  type={p.data_type === "date" ? "date" : "text"}
                  value={params[p.api_name] ?? ""}
                  onChange={(e) => {
                    setParams({ ...params, [p.api_name]: e.target.value });
                    touch();
                  }}
                />
              )}
            </label>
          ))}
          <div>
            <button type="submit" disabled={!action || preview.isPending}>
              {preview.isPending ? t.previewing : t.preview}
            </button>
          </div>
        </form>
      )}
      {preview.isError && <ErrorBox error={preview.error} />}
      {preview.data && <RunCard run={preview.data} open />}
    </div>
  );
}

function RunList({ scope }: { scope: Scope }) {
  const runs = useQuery({
    queryKey: ["action-runs", scope],
    queryFn: () => api.get<ActionRunOut[]>(`/action-runs?scope=${scope}`),
  });
  return (
    <div className="stack">
      {runs.isError && <ErrorBox error={runs.error} />}
      {runs.data?.length === 0 && <p className="muted">{t.none}</p>}
      {runs.data?.map((r) => <RunCard key={r.id} run={r} />)}
    </div>
  );
}

export function RunCard({ run, open = false }: { run: ActionRunOut; open?: boolean }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: ["action-runs"] });
    await qc.invalidateQueries({ queryKey: ["notifications"] });
    await qc.invalidateQueries({ queryKey: ["object"] });
  };
  const decide = useMutation({
    mutationFn: (op: "approve" | "reject" | "rollback") =>
      api.post<ActionRunOut>(`/action-runs/${run.id}/${op}`, op === "reject" ? { reason: reason || null } : {}),
    onSuccess: refresh,
  });
  const latest = decide.data ?? run;
  const pv = latest.preview;
  const when = (v: string | null | undefined) => (v ? new Date(v).toLocaleString() : "—");
  const irreversible = useMemo(() => pv.notify.some((n) => n.irreversible && n.count > 0), [pv]);

  return (
    <details className="card" open={open}>
      <summary className="row">
        <span className={`badge ${latest.status === "executed" ? "succeeded" : latest.status === "failed" || latest.status === "blocked" ? "failed" : ""}`}>
          {t.status[latest.status]}
        </span>
        <strong>{latest.action_display_name}</strong>
        <span>
          {pv.target.title} · <code>{pv.target.pk}</code>
        </span>
        <span className="spacer" />
        <span className="muted">
          {t.requestedBy} {latest.requested_by ?? "—"} · {when(latest.created_at)}
        </span>
      </summary>
      <div className="stack">
        {pv.changes.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>{t.changes}</th>
                <th>{t.before}</th>
                <th>{t.after}</th>
              </tr>
            </thead>
            <tbody>
              {pv.changes.map((c) => (
                <tr key={c.property}>
                  <td>{c.display_name}</td>
                  <td>{c.before}</td>
                  <td>
                    <strong>{c.after}</strong>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {pv.conditions.length > 0 && (
          <div>
            <strong>{t.conditions}</strong>
            <ul>
              {pv.conditions.map((c, i) => (
                <li key={i} className={c.ok ? "" : "error-text"}>
                  {c.ok ? "✓" : "✗"} {c.display_name} {ko.ontology.ops[c.op] ?? c.op}
                  {c.op !== "is_null" && c.op !== "not_null" && ` ${c.expected}`}{" "}
                  <span className="muted">
                    ({t.before} {c.actual})
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {pv.notify.length > 0 && (
          <div>
            <strong>{t.notify}</strong>
            <ul>
              {pv.notify.map((n, i) => (
                <li key={i}>
                  {ko.ontology.channel[n.channel]} · {t.recipients(n.count)}
                  {n.recipients.length > 0 && <span className="muted"> ({n.recipients.join(", ")})</span>}
                  {n.irreversible && <span className="badge failed"> {t.irreversible}</span>}
                  <div className="muted">“{n.body}”</div>
                </li>
              ))}
            </ul>
          </div>
        )}
        {pv.blocked.length > 0 && (
          <div className="error-text">
            <strong>{t.blocked}</strong>
            <ul>
              {pv.blocked.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          </div>
        )}
        {pv.warnings.length > 0 && (
          <div className="notice">
            <strong>{t.warnings}</strong>
            <ul>
              {pv.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}
        {latest.error && (
          <p className="error-text">
            {t.error}: {latest.error}
          </p>
        )}
        {latest.reason && (
          <p className="muted">
            {t.reason}: {latest.reason}
          </p>
        )}
        {latest.decided_by && (
          <p className="muted">
            {t.decidedBy} {latest.decided_by} · {when(latest.decided_at)}
          </p>
        )}
        {latest.deliveries.length > 0 && (
          <div>
            <strong>{t.deliveries}</strong>
            <ul>
              {latest.deliveries.map((d, i) => (
                <li key={i}>
                  {ko.ontology.channel[d.channel]} {t.deliveryKind[d.kind]} · {d.recipient} · {t.deliveryStatus[d.status]}
                  {d.error && <span className="error-text"> — {d.error}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {latest.status === "previewed" && !latest.can_approve && <p className="muted">{t.waiting}</p>}
        <div className="row">
          {latest.can_approve && (
            <button
              type="button"
              disabled={decide.isPending}
              onClick={() => (!irreversible || window.confirm(t.approveConfirm)) && decide.mutate("approve")}
            >
              {t.approve}
            </button>
          )}
          {latest.can_reject && (
            <>
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t.rejectReason} />
              <button type="button" className="link danger" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
                {latest.can_approve ? t.reject : t.withdraw}
              </button>
            </>
          )}
          {latest.can_rollback && (
            <button
              type="button"
              className="link danger"
              disabled={decide.isPending}
              onClick={() => window.confirm(t.rollbackConfirm) && decide.mutate("rollback")}
            >
              {t.rollback}
            </button>
          )}
        </div>
        {decide.isError && <ErrorBox error={decide.error} />}
      </div>
    </details>
  );
}

function NotificationsPanel() {
  const qc = useQueryClient();
  const list = useNotifications();
  const read = useMutation({
    mutationFn: () => api.post("/notifications/read"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
  return (
    <div className="card stack">
      <div className="row">
        <strong>{t.notifications}</strong>
        {list.data && list.data.unread > 0 && <span className="badge failed">{list.data.unread}</span>}
        <span className="spacer" />
        {list.data && list.data.unread > 0 && (
          <button type="button" className="link" onClick={() => read.mutate()}>
            {t.markRead}
          </button>
        )}
      </div>
      {list.isError && <ErrorBox error={list.error} />}
      {list.data?.items.length === 0 && <p className="muted">{t.noNotifications}</p>}
      <ul>
        {list.data?.items.map((n) => (
          <li key={n.id} className={n.read_at ? "muted" : ""}>
            {n.kind === "compensation" && <span className="badge failed">{t.deliveryKind.compensation}</span>} {n.body}{" "}
            <span className="muted">· {new Date(n.created_at).toLocaleString()}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
