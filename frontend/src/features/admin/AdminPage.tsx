import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api } from "../../api/client";
import type {
  AuditChainOut,
  AuditLogOut,
  GroupOut,
  RoleOut,
  SystemOut,
  TargetOut,
  TraceOut,
  V1MetricsOut,
  UserCreate,
  UserOut,
  UserPatch,
} from "../../api/types";
import { useMe } from "../../app/auth";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { GroupsPanel } from "./GroupsPanel";

const t = ko.admin;
type Tab = keyof typeof t.tabs;

export function AdminPage() {
  const [tab, setTab] = useState<Tab>("users");
  return (
    <section className="stack">
      <h1>{t.title}</h1>
      <SystemCard />
      <MetricsCard />
      <TargetsCard />
      <nav className="row" role="tablist">
        {(Object.keys(t.tabs) as Tab[]).map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={tab === k}
            className={tab === k ? "" : "link"}
            onClick={() => setTab(k)}
          >
            {t.tabs[k]}
          </button>
        ))}
      </nav>
      {tab === "users" && <UsersPanel />}
      {tab === "permissions" && <GroupsPanel />}
      {tab === "audit" && <AuditPanel />}
      {tab === "trace" && <TracePanel />}
    </section>
  );
}

/** 지금 버전과 배포 콘솔 링크. 배포(업그레이드·롤백·백업)는 앱 밖의 배포 콘솔이 한다(ADR-0014). */
function SystemCard() {
  const sys = useQuery({ queryKey: ["admin", "system"], queryFn: () => api.get<SystemOut>("/admin/system") });
  if (!sys.data) {
    return null;
  }
  return (
    <div className="card row">
      <span>
        {t.version}: <code>{sys.data.version}</code>
      </span>
      <span className="spacer" />
      {sys.data.deployer_url ? (
        <a href={sys.data.deployer_url} target="_blank" rel="noreferrer">
          {t.deployerOpen}
        </a>
      ) : (
        <span className="muted">{t.deployerNone}</span>
      )}
    </div>
  );
}

/** PRD 7절 v1 지표 — Action 실행 성공률·승인 거부율과 확인되지 않은 편집 충돌. 분모가 0 이면 '—'. */
function MetricsCard() {
  const m = useQuery({ queryKey: ["admin", "metrics"], queryFn: () => api.get<V1MetricsOut>("/admin/metrics") });
  if (m.isError) return <ErrorBox error={m.error} />;
  if (!m.data) return null;
  const tm = t.metrics;
  const pct = (v: number | null) => (v == null ? tm.none : `${(v * 100).toFixed(1)}%`);
  return (
    <div className="card stack">
      <strong>{tm.title(m.data.days)}</strong>
      <div className="row">
        <span title={tm.successHint}>
          {tm.success}: <strong>{pct(m.data.success_rate)}</strong>
        </span>
        <span title={tm.rejectionHint}>
          {tm.rejection}: <strong>{pct(m.data.rejection_rate)}</strong>
        </span>
        <span title={tm.conflictsHint}>
          {tm.conflicts}: <strong>{m.data.open_conflicts}</strong>
        </span>
      </div>
      <div className="muted">
        {tm.counts}:{" "}
        {Object.entries(m.data.counts)
          .map(([k, n]) => `${tm.status[k] ?? k} ${n}`)
          .join(" · ")}
      </div>
    </div>
  );
}

/**
 * PRD 7절 성공 지표의 목표 — 관리자가 정하고(비우면 제안값), 지금 측정값과 견준다. 평가 게이트 기준(gate.yaml)과는
 * 별개다(게이트는 CI 에서 같은 기준으로 돌아야 한다). 비율은 화면에서 %로 다룬다.
 */
function TargetsCard() {
  const qc = useQueryClient();
  const tt = t.targets;
  const q = useQuery({ queryKey: ["admin", "targets"], queryFn: () => api.get<TargetOut[]>("/admin/targets") });
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const save = useMutation({
    mutationFn: (values: Record<string, number | null>) => api.put<TargetOut[]>("/admin/targets", { values }),
    onSuccess: (data) => {
      qc.setQueryData(["admin", "targets"], data);
      setDraft(null);
    },
  });
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return null;
  const ratio = (x: TargetOut) => x.unit === "ratio";
  const show = (x: TargetOut, v: number | null | undefined) =>
    v == null ? tt.unset : ratio(x) ? `${(v * 100).toFixed(1)}%` : `${v}`;
  const toInput = (x: TargetOut) => (x.value == null ? "" : String(ratio(x) ? +(x.value * 100).toFixed(4) : x.value));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!draft) return;
    const values: Record<string, number | null> = {};
    for (const x of q.data) {
      const raw = (draft[x.key] ?? "").trim();
      if (raw === toInput(x)) continue;
      values[x.key] = raw === "" ? null : ratio(x) ? Number(raw) / 100 : Number(raw);
    }
    save.mutate(values);
  };
  return (
    <form className="card stack" onSubmit={submit}>
      <div className="row">
        <strong>{tt.title}</strong>
        <span className="spacer" />
        {draft ? (
          <>
            <button type="submit" disabled={save.isPending}>
              {tt.save}
            </button>
            <button type="button" className="link" onClick={() => setDraft(null)}>
              {tt.cancel}
            </button>
          </>
        ) : (
          <button
            type="button"
            className="link"
            onClick={() => setDraft(Object.fromEntries(q.data.map((x) => [x.key, toInput(x)])))}
          >
            {tt.edit}
          </button>
        )}
      </div>
      <p className="muted">{tt.hint}</p>
      {save.isError && <ErrorBox error={save.error} />}
      <table>
        <thead>
          <tr>
            <th>{tt.metric}</th>
            <th>{tt.target}</th>
            <th>{tt.measured}</th>
            <th>{tt.status}</th>
            <th>{tt.source}</th>
          </tr>
        </thead>
        <tbody>
          {q.data.map((x) => (
            <tr key={x.key}>
              <td>{x.label}</td>
              <td>
                {tt.direction[x.direction]}{" "}
                {draft ? (
                  <input
                    aria-label={x.label}
                    inputMode="decimal"
                    size={6}
                    value={draft[x.key] ?? ""}
                    placeholder={x.default == null ? tt.unset : show(x, x.default)}
                    onChange={(e) => setDraft({ ...draft, [x.key]: e.target.value })}
                  />
                ) : (
                  <>
                    {show(x, x.value)}
                    {!x.custom && x.value != null && <span className="muted"> ({tt.proposed})</span>}
                  </>
                )}
                {draft && ratio(x) && " %"}
              </td>
              <td>{show(x, x.measured)}</td>
              <td>{x.met == null ? "—" : x.met ? tt.met : <span className="error-text">{tt.unmet}</span>}</td>
              <td className="muted">{x.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </form>
  );
}

// ---------------------------------------------------------------- 사용자

function useRoles() {
  return useQuery({ queryKey: ["admin", "roles"], queryFn: () => api.get<RoleOut[]>("/admin/roles") });
}

function UsersPanel() {
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: () => api.get<UserOut[]>("/admin/users") });
  const roles = useRoles();
  const me = useMe();
  return (
    <div className="stack">
      <p className="muted">
        {t.sessionNote} {t.selfNote}
      </p>
      {users.isError && <ErrorBox error={users.error} />}
      <table>
        <thead>
          <tr>
            <th>{t.username}</th>
            <th>{t.displayName}</th>
            <th>{t.auth}</th>
            <th>{t.roles}</th>
            <th>{t.groupsCol}</th>
            <th>{t.status}</th>
            <th>{t.contacts}</th>
          </tr>
        </thead>
        <tbody>
          {users.data?.map((u) => (
            <UserRow key={u.id} u={u} allRoles={roles.data ?? []} self={me.data?.id === u.id} />
          ))}
        </tbody>
      </table>
      <details className="card">
        <summary>{t.addUser}</summary>
        <UserForm allRoles={roles.data ?? []} />
      </details>
    </div>
  );
}

function UserRow({ u, allRoles, self }: { u: UserOut; allRoles: RoleOut[]; self: boolean }) {
  const qc = useQueryClient();
  const [picked, setPicked] = useState<string[]>(u.roles);
  const patch = useMutation({
    mutationFn: (body: UserPatch) => api.patch<UserOut>(`/admin/users/${u.id}`, body),
    onSuccess: (next) => {
      setPicked(next.roles);
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });
  const changed = [...picked].sort().join() !== [...u.roles].sort().join();
  const toggle = (name: string) =>
    setPicked((prev) => (prev.includes(name) ? prev.filter((r) => r !== name) : [...prev, name]));
  return (
    <tr>
      <td>
        <code>{u.username ?? "—"}</code>
      </td>
      <td>{u.display_name}</td>
      <td>{u.auth}</td>
      <td>
        <div className="row">
          {allRoles.map((r) => (
            <label key={r.id} className="check">
              <input type="checkbox" checked={picked.includes(r.name)} onChange={() => toggle(r.name)} />
              {r.name}
            </label>
          ))}
          {changed && (
            <button type="button" onClick={() => patch.mutate({ roles: picked })} disabled={patch.isPending}>
              {t.saveRoles}
            </button>
          )}
        </div>
        {patch.isError && <ErrorBox error={patch.error} />}
      </td>
      <td className="muted">{u.groups.join(", ") || "—"}</td>
      <td>
        <span className={`badge ${u.status === "active" ? "succeeded" : "failed"}`}>
          {u.status === "active" ? t.active : t.disabled}
        </span>{" "}
        {!self && (
          <button
            type="button"
            className="link"
            onClick={() => patch.mutate({ status: u.status === "active" ? "disabled" : "active" })}
          >
            {u.status === "active" ? t.disable : t.enable}
          </button>
        )}
      </td>
      <td>
        <ContactsCell u={u} />
      </td>
    </tr>
  );
}

/** Action 알림(메일·SMS) 연락처. 목록에는 마스킹한 값만 온다 — 고칠 때는 새로 적는다. */
function ContactsCell({ u }: { u: UserOut }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const save = useMutation({
    mutationFn: () => api.put<UserOut>(`/admin/users/${u.id}/contacts`, { email: email || null, phone: phone || null }),
    onSuccess: () => {
      setEditing(false);
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });
  if (!editing) {
    return (
      <span>
        <span className="muted">{[u.email, u.phone].filter(Boolean).join(" · ") || "—"}</span>{" "}
        <button type="button" className="link" onClick={() => setEditing(true)}>
          {t.editContacts}
        </button>
      </span>
    );
  }
  return (
    <div className="stack">
      <p className="muted">{t.contactsHint}</p>
      <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder={t.email} />
      <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder={t.phone} />
      <div className="row">
        <button type="button" onClick={() => save.mutate()} disabled={save.isPending}>
          {ko.ontology.save}
        </button>
        <button type="button" className="link" onClick={() => setEditing(false)}>
          {ko.ontology.cancel}
        </button>
      </div>
      {save.isError && <ErrorBox error={save.error} />}
    </div>
  );
}

function UserForm({ allRoles }: { allRoles: RoleOut[] }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<UserCreate>({ username: "", display_name: "", password: "", roles: ["user"], groups: [] });
  const groups = useQuery({ queryKey: ["admin", "groups"], queryFn: () => api.get<GroupOut[]>("/admin/groups") });
  const create = useMutation({
    mutationFn: (body: UserCreate) => api.post<UserOut>("/admin/users", body),
    onSuccess: () => {
      setForm({ username: "", display_name: "", password: "", roles: ["user"], groups: [] });
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(form);
  };
  const roles = form.roles ?? [];
  const picked = form.groups ?? [];
  return (
    <form className="grid-form" onSubmit={submit}>
      <label>
        {t.username}
        <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
      </label>
      <label>
        {t.displayName}
        <input
          value={form.display_name}
          onChange={(e) => setForm({ ...form, display_name: e.target.value })}
          required
        />
      </label>
      <label>
        {t.password}
        <input
          type="password"
          autoComplete="new-password"
          value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })}
          required
        />
      </label>
      <div className="row span-all">
        {allRoles.map((r) => (
          <label key={r.id} className="check">
            <input
              type="checkbox"
              checked={roles.includes(r.name)}
              onChange={() =>
                setForm({
                  ...form,
                  roles: roles.includes(r.name) ? roles.filter((x) => x !== r.name) : [...roles, r.name],
                })
              }
            />
            {r.name}
          </label>
        ))}
      </div>
      <div className="row span-all">
        <span className="muted">{t.groupsCol}:</span>
        {(groups.data ?? []).map((g) => (
          <label key={g.id} className="check">
            <input
              type="checkbox"
              checked={picked.includes(g.name)}
              onChange={() =>
                setForm({
                  ...form,
                  groups: picked.includes(g.name) ? picked.filter((x) => x !== g.name) : [...picked, g.name],
                })
              }
            />
            {g.display_name}
          </label>
        ))}
        <button type="submit" disabled={create.isPending}>
          {ko.common.add}
        </button>
      </div>
      {create.isError && (
        <div className="span-all">
          <ErrorBox error={create.error} />
        </div>
      )}
    </form>
  );
}

// ---------------------------------------------------------------- 감사 로그

/** 감사 로그 해시 체인 검증(PRD 5.5) — 누르면 지금 검증한다. 끊긴 곳은 보고만 한다(추가 전용). */
function AuditChainCheck() {
  const check = useMutation({ mutationFn: () => api.get<AuditChainOut>("/admin/audit-chain") });
  const r = check.data;
  const why = (x: AuditChainOut) => t.chain.reason[x.reason ?? ""] ?? x.reason ?? "";
  return (
    <div className="row">
      <button type="button" disabled={check.isPending} onClick={() => check.mutate()}>
        {t.chain.check}
      </button>
      {r?.ok && <span className="ok-text">{t.chain.ok(r.rows, r.last_seq)}</span>}
      {r && !r.ok && <span className="error-text">{t.chain.broken(why(r), r.broken_seq, r.broken_id)}</span>}
      {check.isError && <ErrorBox error={check.error} />}
    </div>
  );
}

const PAGE = 50;

function AuditPanel() {
  const [event, setEvent] = useState("");
  const logs = useInfiniteQuery({
    queryKey: ["admin", "audit", event],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => {
      const q = new URLSearchParams({ limit: String(PAGE) });
      if (event) q.set("event", event);
      if (pageParam !== null) q.set("before_id", String(pageParam));
      return api.get<AuditLogOut[]>(`/audit-logs?${q.toString()}`);
    },
    getNextPageParam: (last) => (last.length === PAGE ? last.at(-1)?.id : undefined),
    // 조회 자체가 감사 기록을 남기므로 창 포커스마다 다시 읽지 않는다.
    refetchOnWindowFocus: false,
  });
  const rows = logs.data?.pages.flat() ?? [];
  return (
    <div className="stack">
      <p className="muted">{t.auditNote}</p>
      <AuditChainCheck />
      <label>
        {t.filterEvent}
        <input value={event} onChange={(e) => setEvent(e.target.value.trim())} placeholder="auth.login" />
      </label>
      {logs.isError && <ErrorBox error={logs.error} />}
      <table>
        <thead>
          <tr>
            <th>{t.ts}</th>
            <th>{t.event}</th>
            <th>{t.actor}</th>
            <th>{t.target}</th>
            <th>{t.payload}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{new Date(r.ts).toLocaleString()}</td>
              <td>
                <code>{r.event}</code>
              </td>
              <td>{r.actor_id ?? "—"}</td>
              <td>{r.target ?? "—"}</td>
              <td>
                <code>{JSON.stringify(r.payload)}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {logs.hasNextPage && (
        <button type="button" onClick={() => void logs.fetchNextPage()} disabled={logs.isFetchingNextPage}>
          {t.more}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 트레이스

function TracePanel() {
  const [input, setInput] = useState("");
  const [id, setId] = useState<number | null>(null);
  const trace = useQuery({
    queryKey: ["admin", "trace", id],
    queryFn: () => api.get<TraceOut>(`/traces/${id}`),
    enabled: id !== null,
    refetchOnWindowFocus: false,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const n = Number(input);
    if (Number.isInteger(n) && n > 0) setId(n);
  };
  return (
    <div className="stack">
      <p className="muted">{t.traceNote}</p>
      <form className="row" onSubmit={submit}>
        <label>
          {t.traceId}
          <input value={input} onChange={(e) => setInput(e.target.value)} inputMode="numeric" />
        </label>
        <button type="submit">{t.open}</button>
      </form>
      {trace.isError && <ErrorBox error={trace.error} />}
      {trace.data && <TraceView tr={trace.data} />}
    </div>
  );
}

function TraceView({ tr }: { tr: TraceOut }) {
  return (
    <div className="card stack">
      <div className="row">
        <strong>#{tr.id}</strong>
        <span className={`badge ${tr.status === "failed" ? "failed" : "succeeded"}`}>
          {tr.status}
          {tr.failure_reason ? ` · ${tr.failure_reason}` : ""}
          {tr.grade ? ` · ${tr.grade}` : ""}
        </span>
        <span className="muted">{new Date(tr.created_at).toLocaleString()}</span>
      </div>
      <details open>
        <summary>{t.plan}</summary>
        <pre>{JSON.stringify(tr.plan, null, 2)}</pre>
      </details>
      {tr.sql_masked && <pre>{tr.sql_masked}</pre>}
      {tr.cypher_masked && <pre>{tr.cypher_masked}</pre>}
      <div>
        {t.latency}: <code>{JSON.stringify(tr.stage_latency_ms)}</code>
      </div>
      {tr.removed_citations.length > 0 && (
        <details>
          <summary>
            {t.removed} ({tr.removed_citations.length})
          </summary>
          <pre>{JSON.stringify(tr.removed_citations, null, 2)}</pre>
        </details>
      )}
      <details>
        <summary>
          {t.llmCalls} ({tr.llm_calls.length})
        </summary>
        <pre>{JSON.stringify(tr.llm_calls, null, 2)}</pre>
      </details>
    </div>
  );
}
