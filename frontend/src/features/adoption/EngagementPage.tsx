import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../../api/client";
import type { EngagementDetail, MemberOut, SheetKindOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { engagementKey, useEngagement, useSheetKinds } from "./queries";

const t = ko.adoption;

export function EngagementPage() {
  const { engagementId = "" } = useParams();
  const detail = useEngagement(engagementId);
  const kinds = useSheetKinds();
  const [stage, setStage] = useState(1);

  if (detail.isPending || kinds.isPending) return <p>{ko.common.loading}</p>;
  if (detail.isError) return <ErrorBox error={detail.error} />;
  if (kinds.isError) return <ErrorBox error={kinds.error} />;
  const e = detail.data;
  const current = e.stages.find((s) => s.stage === stage) ?? e.stages[0];
  if (!current) return null;
  const roadmap = kinds.data.find((k) => k.stage === 0);

  return (
    <section className="stack">
      <p>
        <Link to="/adoption">← {t.title}</Link>
      </p>
      <h1>{e.name}</h1>
      {e.description && <p className="muted">{e.description}</p>}
      <p className="muted">
        {t.myRole}: {t.roles[e.my_role]}
      </p>
      {e.can_manage && <InfoForm e={e} />}
      {roadmap && <SheetLinks e={e} kinds={[roadmap]} />}

      <nav className="row tabs" role="tablist">
        {e.stages.map((s) => (
          <button
            key={s.stage}
            type="button"
            role="tab"
            aria-selected={s.stage === current.stage}
            className={`tab${s.stage === current.stage ? " active" : ""}`}
            onClick={() => setStage(s.stage)}
          >
            {s.stage} {s.title} {s.done ? "✓" : ""}
          </button>
        ))}
      </nav>
      <div className="card stack">
        <strong>{t.handoff}</strong>
        <ul>
          {current.handoffs.map((h) => (
            <li key={h.label}>
              <span className={h.filled ? "ok-text" : "warn-text"}>{h.filled ? `✓ ${t.filled}` : `— ${t.missing}`}</span> {h.label}
            </li>
          ))}
        </ul>
        {current.done && <span className="badge succeeded">{t.stageDone}</span>}
      </div>
      <SheetLinks e={e} kinds={kinds.data.filter((k) => k.stage === current.stage)} />
      <Members e={e} />
    </section>
  );
}

function SheetLinks({ e, kinds }: { e: EngagementDetail; kinds: SheetKindOut[] }) {
  const revisions = new Map(e.sheets.map((s) => [s.kind, s]));
  return (
    <table>
      <tbody>
        {kinds.map((k) => {
          const saved = revisions.get(k.kind);
          return (
            <tr key={k.kind}>
              <td style={{ width: "5em" }}>{k.number}</td>
              <td>
                <Link to={`/adoption/${e.id}/sheets/${k.kind}`}>{k.title}</Link>
              </td>
              <td className="muted">
                {saved
                  ? `${t.revision} ${saved.revision} · ${saved.updated_at ? new Date(saved.updated_at).toLocaleString("ko-KR") : ""}`
                  : t.notSaved}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function InfoForm({ e }: { e: EngagementDetail }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(e.name);
  const [description, setDescription] = useState(e.description ?? "");
  const save = useMutation({
    mutationFn: () => api.patch<EngagementDetail>(`/adoption/engagements/${e.id}`, { name: name.trim(), description }),
    onSuccess: (d) => {
      qc.setQueryData(engagementKey(e.id), d);
      setOpen(false);
    },
  });
  if (!open) {
    return (
      <p>
        <button type="button" className="link" onClick={() => setOpen(true)}>
          {t.editInfo}
        </button>
      </p>
    );
  }
  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    save.mutate();
  };
  return (
    <form className="row" onSubmit={submit}>
      <input aria-label={t.name} value={name} maxLength={200} onChange={(ev) => setName(ev.target.value)} />
      <input aria-label={t.description} value={description} maxLength={4000} onChange={(ev) => setDescription(ev.target.value)} />
      <button type="submit" disabled={save.isPending || !name.trim()}>
        {ko.common.save}
      </button>
      <button type="button" className="link" onClick={() => setOpen(false)}>
        {ko.common.cancel}
      </button>
      {save.isError && <ErrorBox error={save.error} />}
    </form>
  );
}

function Members({ e }: { e: EngagementDetail }) {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<MemberOut["role"]>("editor");
  const refresh = () => qc.invalidateQueries({ queryKey: engagementKey(e.id) });
  const add = useMutation({
    mutationFn: () => api.post<MemberOut>(`/adoption/engagements/${e.id}/members`, { username: username.trim(), role }),
    onSuccess: async () => {
      setUsername("");
      await refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (userId: number) => api.del(`/adoption/engagements/${e.id}/members/${userId}`),
    onSuccess: refresh,
  });
  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    add.mutate();
  };
  return (
    <section className="stack">
      <h2>{t.members}</h2>
      <table>
        <tbody>
          {e.members.map((m) => (
            <tr key={m.user_id}>
              <td>{m.display_name}</td>
              <td className="muted">{m.username ?? "—"}</td>
              <td>{t.roles[m.role]}</td>
              <td className="actions">
                {e.can_manage && (
                  <button type="button" className="link" onClick={() => remove.mutate(m.user_id)}>
                    {t.remove}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {e.can_manage && (
        <form className="row" onSubmit={submit}>
          <input aria-label={t.username} placeholder={t.username} value={username} maxLength={64} onChange={(ev) => setUsername(ev.target.value)} />
          <select aria-label={t.myRole} value={role} onChange={(ev) => setRole(ev.target.value as MemberOut["role"])}>
            {(["owner", "editor", "viewer"] as const).map((r) => (
              <option key={r} value={r}>
                {t.roles[r]}
              </option>
            ))}
          </select>
          <button type="submit" disabled={add.isPending || !username.trim()}>
            {t.addMember}
          </button>
        </form>
      )}
      {add.isError && <ErrorBox error={add.error} />}
      {remove.isError && <ErrorBox error={remove.error} />}
    </section>
  );
}
