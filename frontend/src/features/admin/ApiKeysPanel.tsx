import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api } from "../../api/client";
import type { ApiKeyIssued, ApiKeyOut, UserOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { Pager, usePaged } from "../../components/Pager";
import { formatDate, formatDateTime } from "../../app/format";
import { confirmDialog, toast } from "../../components/feedback";
import { EmptyState, PageHeader, RowMenu } from "../../components/page";
import { CreateDialogButton, DialogActions } from "../../components/Dialog";

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
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    void qc.invalidateQueries({ queryKey: ["admin", "api-keys"] });
  };
  const revoke = useMutation({ mutationFn: (id: number) => api.del(`/admin/api-keys/${id}`), onSuccess: refresh });
  const { rows: pagedRows, pager } = usePaged(keys.data);
  return (
    <>
      <PageHeader
        id="admin-api-keys"
        title={ko.admin.sections["api-keys"]}
        intro={t.hint}
        actions={
          <>
            <CreateDialogButton className="secondary" label={`+ ${t.newAccount}`}>
              {(close) => <ServiceAccountForm onDone={refresh} onClose={close} />}
            </CreateDialogButton>
            {services.length > 0 && (
              <CreateDialogButton label={`+ ${t.issue}`}>
                {(close) => <IssueForm services={services} onIssued={refresh} onClose={close} />}
              </CreateDialogButton>
            )}
          </>
        }
      />
      {(keys.isError || revoke.isError) && <ErrorBox error={keys.error ?? revoke.error} />}
      {keys.data?.length === 0 && <EmptyState message={t.none} />}
      {keys.data && keys.data.length > 0 && (
        <>
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
            {pagedRows.map((k) => {
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
                    {k.expires_at && !k.revoked_at && <div className="muted">{t.until(formatDate(k.expires_at))}</div>}
                  </td>
                  <td>{k.last_used_at ? formatDateTime(k.last_used_at) : "—"}</td>
                  <td>
                    {!k.revoked_at && (
                      <RowMenu
                        items={[
                          {
                            label: t.revoke,
                            danger: true,
                            disabled: revoke.isPending,
                            onSelect: () =>
                              void confirmDialog(t.confirmRevoke(k.name), { danger: true, confirmLabel: t.revoke }).then((ok) => ok && revoke.mutate(k.id, { onSuccess: () => toast(ko.common.done) })),
                          },
                        ]}
                      />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <Pager pager={pager} />
        </>
      )}
    </>
  );
}

function ServiceAccountForm({ onDone, onClose }: { onDone: () => void; onClose: () => void }) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<UserOut>("/admin/service-accounts", { username, display_name: displayName }),
    onSuccess: () => {
      onDone();
      toast(ko.common.created);
      onClose();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <form className="stack" onSubmit={submit}>
      <span className="muted">{t.accountHint}</span>
      <div className="grid-form">
        <label>
          {t.username}
          <input value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label>
          {t.displayName}
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} required />
        </label>
      </div>
      {create.isError && <ErrorBox error={create.error} />}
      <DialogActions onCancel={onClose} pending={create.isPending} submitLabel={t.create} />
    </form>
  );
}

/** 키 발급 — 발급하면 같은 창이 키 원문(한 번만 보인다)으로 바뀐다. 닫으면 다시 볼 수 없다. */
function IssueForm({ services, onIssued, onClose }: { services: UserOut[]; onIssued: () => void; onClose: () => void }) {
  const [issued, setIssued] = useState<ApiKeyIssued | null>(null);
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
      setIssued(k);
      onIssued();
    },
  });
  const toggle = (s: string) => setScopes((xs) => (xs.includes(s) ? xs.filter((x) => x !== s) : [...xs, s]));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    issue.mutate();
  };
  if (issued) {
    return (
      <div className="stack">
        <div className="notice warn stack">
          <strong>{t.issuedTitle(issued.name)}</strong>
          <code className="secret">{issued.key}</code>
          <span>{t.issuedHint}</span>
        </div>
        <div className="row dialog-actions">
          <span className="spacer" />
          <button type="button" onClick={onClose}>
            {t.issuedDone}
          </button>
        </div>
      </div>
    );
  }
  return (
    <form className="stack" onSubmit={submit}>
      <div className="grid-form">
        <label>
          {t.account}
          <select value={chosen} onChange={(e) => setUserId(e.target.value)}>
            {services.map((u) => (
              <option key={u.id} value={u.id}>
                {u.display_name} ({u.username})
              </option>
            ))}
          </select>
        </label>
        <label>
          {t.name}
          <input value={name} maxLength={64} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          {t.days}
          <input type="number" min={1} max={3650} value={days} onChange={(e) => setDays(e.target.value)} />
        </label>
      </div>
      <div className="row">
        <span className="field-title">{t.scopes}</span>
        {SCOPES.map((s) => (
          <label key={s} className="check">
            <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} />
            {t.scopeName[s]}
          </label>
        ))}
      </div>
      {issue.isError && <ErrorBox error={issue.error} />}
      <DialogActions onCancel={onClose} pending={issue.isPending} disabled={scopes.length === 0 || !chosen} submitLabel={t.issueButton} />
    </form>
  );
}
