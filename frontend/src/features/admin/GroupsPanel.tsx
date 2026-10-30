import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api } from "../../api/client";
import type { GroupOut, TypeGrantIO, TypeOut, UserOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.admin.groups;
const FLAGS = ["can_read", "can_create", "can_edit", "can_delete"] as const;
type Flag = (typeof FLAGS)[number];

/**
 * 데이터 그룹(Phase 14) — 역할은 화면·기능, 그룹은 데이터 권한이다. 사용자의 권한은 소속 그룹의 합집합이다.
 * 구성원·권한 저장은 교체(PUT)이고, 변경은 감사 로그에 남는다.
 */
export function GroupsPanel() {
  const groups = useQuery({ queryKey: ["admin", "groups"], queryFn: () => api.get<GroupOut[]>("/admin/groups") });
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: () => api.get<UserOut[]>("/admin/users") });
  const types = useQuery({ queryKey: ["admin", "object-types"], queryFn: () => api.get<TypeOut[]>("/admin/object-types") });
  const links = useQuery({ queryKey: ["admin", "link-types"], queryFn: () => api.get<TypeOut[]>("/admin/link-types") });
  const err = [groups, users, types, links].find((q) => q.isError)?.error;
  if (err) return <ErrorBox error={err} />;
  if (!groups.data || !users.data || !types.data || !links.data) return <p>{ko.common.loading}</p>;
  return (
    <div className="stack">
      <p className="muted">{t.hint}</p>
      {types.data.length === 0 && <p className="notice">{ko.admin.noTypes}</p>}
      {groups.data.map((g) => (
        <GroupCard key={g.id} g={g} users={users.data} types={types.data} links={links.data} />
      ))}
      <details className="card">
        <summary>{t.add}</summary>
        <GroupForm />
      </details>
    </div>
  );
}

function GroupForm() {
  const qc = useQueryClient();
  const [form, setForm] = useState({ name: "", display_name: "", external_id: "" });
  const create = useMutation({
    mutationFn: () =>
      api.post<GroupOut>("/admin/groups", {
        name: form.name,
        display_name: form.display_name,
        external_id: form.external_id.trim() || null,
      }),
    onSuccess: () => {
      setForm({ name: "", display_name: "", external_id: "" });
      void qc.invalidateQueries({ queryKey: ["admin", "groups"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <form className="grid-form" onSubmit={submit}>
      <label>
        {t.name}
        <input value={form.name} pattern="[a-z][a-z0-9_]{0,63}" onChange={(e) => setForm({ ...form, name: e.target.value })} required />
      </label>
      <label>
        {t.displayName}
        <input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} required />
      </label>
      <label>
        {t.externalId}
        <input value={form.external_id} onChange={(e) => setForm({ ...form, external_id: e.target.value })} />
      </label>
      <div className="row span-all">
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

function grantMap(g: GroupOut): Record<string, Record<Flag, boolean>> {
  return Object.fromEntries(
    g.types.map((x) => [
      x.object_type,
      { can_read: !!x.can_read, can_create: !!x.can_create, can_edit: !!x.can_edit, can_delete: !!x.can_delete },
    ]),
  );
}

function GroupCard({ g, users, types, links }: { g: GroupOut; users: UserOut[]; types: TypeOut[]; links: TypeOut[] }) {
  const qc = useQueryClient();
  const refresh = () => void qc.invalidateQueries({ queryKey: ["admin"] });
  const [members, setMembers] = useState<number[]>(g.member_ids);
  const [grants, setGrants] = useState(() => grantMap(g));
  const [linkSet, setLinkSet] = useState<string[]>(g.links);
  const saveMembers = useMutation({
    mutationFn: () => api.put<GroupOut>(`/admin/groups/${g.id}/members`, { user_ids: members }),
    onSuccess: refresh,
  });
  const savePerms = useMutation({
    mutationFn: () =>
      api.put<GroupOut>(`/admin/groups/${g.id}/permissions`, {
        types: Object.entries(grants).map(([object_type, f]): TypeGrantIO => ({ object_type, ...f })),
        links: linkSet,
      }),
    onSuccess: refresh,
  });
  const del = useMutation({ mutationFn: () => api.del(`/admin/groups/${g.id}`), onSuccess: refresh });
  // 게시본에서 빠진 타입·링크에 남은 권한도 보여 준다 — 보이지 않으면 지울 수 없다.
  const typeRows = [
    ...types,
    ...Object.keys(grants)
      .filter((x) => !types.some((ty) => ty.api_name === x))
      .map((x) => ({ api_name: x, display_name: t.unpublished })),
  ];
  const linkRows = [
    ...links,
    ...linkSet.filter((x) => !links.some((l) => l.api_name === x)).map((x) => ({ api_name: x, display_name: t.unpublished })),
  ];
  const flip = (type: string, f: Flag) =>
    setGrants((prev) => {
      const cur = prev[type] ?? { can_read: false, can_create: false, can_edit: false, can_delete: false };
      const next = { ...cur, [f]: !cur[f] };
      // 쓰기 권한은 읽을 수 있어야 쓸모가 있다 — 쓰기를 켜면 읽기도 켠다.
      if (f !== "can_read" && next[f]) next.can_read = true;
      return { ...prev, [type]: next };
    });
  const membersChanged = [...members].sort().join() !== [...g.member_ids].sort().join();
  return (
    <div className="card stack">
      <div className="row">
        <strong>{g.display_name}</strong> <code>{g.name}</code>
        {g.external_id && (
          <span className="badge" title={t.externalHint}>
            SSO {g.external_id}
          </span>
        )}
        <span className="spacer" />
        <button
          type="button"
          className="link danger"
          onClick={() => window.confirm(t.confirmDelete(g.display_name)) && del.mutate()}
        >
          {ko.common.delete}
        </button>
      </div>
      <details>
        <summary>{t.members(members.length)}</summary>
        <div className="row">
          {users.map((u) => (
            <label key={u.id} className="check">
              <input
                type="checkbox"
                checked={members.includes(u.id)}
                onChange={() => setMembers((m) => (m.includes(u.id) ? m.filter((x) => x !== u.id) : [...m, u.id]))}
              />
              {u.display_name} <code>{u.username ?? u.id}</code>
            </label>
          ))}
        </div>
        <button type="button" disabled={!membersChanged || saveMembers.isPending} onClick={() => saveMembers.mutate()}>
          {t.saveMembers}
        </button>
      </details>
      <table>
        <thead>
          <tr>
            <th>{t.type}</th>
            {FLAGS.map((f) => (
              <th key={f}>{t.flag[f]}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {typeRows.map((ty) => (
            <tr key={ty.api_name}>
              <td>
                {ty.display_name} <code>{ty.api_name}</code>
              </td>
              {FLAGS.map((f) => (
                <td key={f}>
                  <input
                    type="checkbox"
                    aria-label={`${ty.api_name} ${t.flag[f]}`}
                    checked={!!grants[ty.api_name]?.[f]}
                    onChange={() => flip(ty.api_name, f)}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {linkRows.length > 0 && (
        <div className="row">
          <span>{t.link}:</span>
          {linkRows.map((l) => (
            <label key={l.api_name} className="check">
              <input
                type="checkbox"
                checked={linkSet.includes(l.api_name)}
                onChange={() =>
                  setLinkSet((s) => (s.includes(l.api_name) ? s.filter((x) => x !== l.api_name) : [...s, l.api_name]))
                }
              />
              {l.display_name} <code>{l.api_name}</code>
            </label>
          ))}
        </div>
      )}
      <div className="row">
        <button type="button" disabled={savePerms.isPending} onClick={() => savePerms.mutate()}>
          {t.savePermissions}
        </button>
        {savePerms.isSuccess && <span className="ok-text">{ko.admin.saved}</span>}
      </div>
      {[saveMembers, savePerms, del].map((m, i) => m.isError && <ErrorBox key={i} error={m.error} />)}
    </div>
  );
}
