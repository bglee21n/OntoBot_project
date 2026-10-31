import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api } from "../../api/client";
import type { ApiKeyIssued, ApiKeyOut, UserOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.admin.apiKeys;
const SCOPES = ["query", "objects:read", "actions:request"] as const;

/**
 * 공개 REST API(Phase 17b) — 서비스 계정과 그 API 키. 키 원문은 발급 직후 한 번만 보인다(서버에는 해시만).
 * 서비스 계정의 역할·데이터 그룹은 사용자·데이터 그룹 탭에서 사람과 똑같이 준다 — 키의 스코프는 그 위에서 좁히기만 한다.
 */
export function ApiKeysPanel() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: () => api.get<UserOut[]>("/admin/users") });
  const keys = useQuery({ queryKey: ["admin", "api-keys"], queryFn: () => api.get<ApiKeyOut[]>("/admin/api-keys") });
  const services = users.data?.filter((u) => u.auth === "service") ?? [];
  const [issued, setIssued] = useState<ApiKeyIssued | null>(null);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    void qc.invalidateQueries({ queryKey: ["admin", "api-keys"] });
  };
  const revoke = useMutation({ mutationFn: (id: number) => api.del(`/admin/api-keys/${id}`), onSuccess: refresh });
  return (
    <div className="stack">
      <p className="muted">{t.hint}</p>
      <ServiceAccountForm onDone={refresh} />
      {services.length > 0 && <IssueForm services={services} onIssued={(k) => (setIssued(k), refresh())} />}
      {issued && (
        <div className="notice warn stack">
          <strong>{t.issuedTitle(issued.name)}</strong>
          <code className="secret">{issued.key}</code>
          <span>{t.issuedHint}</span>
          <button type="button" className="link" onClick={() => setIssued(null)}>
            {t.issuedDone}
          </button>
        </div>
      )}
      {(keys.isError || revoke.isError) && <ErrorBox error={keys.error ?? revoke.error} />}
      {keys.data?.length === 0 && <p className="muted">{t.none}</p>}
      {keys.data && keys.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>{t.name}</th>
              <th>{t.account}</th>
              <th>{t.scopes}</th>
              <th>{t.state}</th>
              <th>{t.lastUsed}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.data.map((k) => {
              const expired = k.expires_at != null && new Date(k.expires_at) <= new Date();
              const state = k.revoked_at ? t.revoked : expired ? t.expired : t.active;
              return (
                <tr key={k.id}>
                  <td>
                    {k.name}
                    <div className="muted">
                      <code>obk_{k.prefix}_…</code>
                    </div>
                  </td>
                  <td>{k.username}</td>
                  <td>{k.scopes.map((s) => t.scopeName[s] ?? s).join(", ")}</td>
                  <td>
                    {state}
                    {k.expires_at && !k.revoked_at && <div className="muted">{t.until(new Date(k.expires_at).toLocaleDateString("ko-KR"))}</div>}
                  </td>
                  <td>{k.last_used_at ? new Date(k.last_used_at).toLocaleString("ko-KR") : "—"}</td>
                  <td>
                    {!k.revoked_at && (
                      <button
                        type="button"
                        className="link"
                        disabled={revoke.isPending}
                        onClick={() => window.confirm(t.confirmRevoke(k.name)) && revoke.mutate(k.id)}
                      >
                        {t.revoke}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ServiceAccountForm({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<UserOut>("/admin/service-accounts", { username, display_name: displayName }),
    onSuccess: () => {
      setUsername("");
      setDisplayName("");
      onDone();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <form className="card stack" onSubmit={submit}>
      <strong>{t.newAccount}</strong>
      <span className="muted">{t.accountHint}</span>
      <div className="row">
        <input aria-label={t.username} placeholder={t.username} value={username} onChange={(e) => setUsername(e.target.value)} required />
        <input aria-label={t.displayName} placeholder={t.displayName} value={displayName} onChange={(e) => setDisplayName(e.target.value)} required />
        <button type="submit" disabled={create.isPending}>
          {t.create}
        </button>
      </div>
      {create.isError && <ErrorBox error={create.error} />}
    </form>
  );
}

function IssueForm({ services, onIssued }: { services: UserOut[]; onIssued: (k: ApiKeyIssued) => void }) {
  const [userId, setUserId] = useState("");
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["query"]);
  const [days, setDays] = useState("");
  const chosen = userId || String(services[0]?.id ?? "");
  const issue = useMutation({
    mutationFn: () =>
      api.post<ApiKeyIssued>("/admin/api-keys", {
        user_id: Number(chosen),
        name,
        scopes,
        expires_days: days ? Number(days) : null,
      }),
    onSuccess: (k) => {
      setName("");
      onIssued(k);
    },
  });
  const toggle = (s: string) => setScopes((xs) => (xs.includes(s) ? xs.filter((x) => x !== s) : [...xs, s]));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    issue.mutate();
  };
  return (
    <form className="card stack" onSubmit={submit}>
      <strong>{t.issue}</strong>
      <div className="row">
        <label className="row">
          {t.account}
          <select value={chosen} onChange={(e) => setUserId(e.target.value)}>
            {services.map((u) => (
              <option key={u.id} value={u.id}>
                {u.display_name} ({u.username})
              </option>
            ))}
          </select>
        </label>
        <input aria-label={t.name} placeholder={t.name} value={name} maxLength={64} onChange={(e) => setName(e.target.value)} required />
        <input aria-label={t.days} placeholder={t.days} type="number" min={1} max={3650} value={days} onChange={(e) => setDays(e.target.value)} />
      </div>
      <div className="row">
        {SCOPES.map((s) => (
          <label key={s} className="row">
            <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} />
            {t.scopeName[s]}
          </label>
        ))}
        <button type="submit" disabled={issue.isPending || scopes.length === 0 || !chosen}>
          {t.issueButton}
        </button>
      </div>
      {issue.isError && <ErrorBox error={issue.error} />}
    </form>
  );
}
