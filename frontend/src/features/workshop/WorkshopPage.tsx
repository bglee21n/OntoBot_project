import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import type { SessionDetail, SessionSummary } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { useMe } from "../../app/auth";
import { ko } from "../../i18n/ko";
import { MergePanel } from "./MergePanel";
import { Pager, usePaged } from "../../components/Pager";
import { formatDateTime } from "../../app/format";
import { EmptyState, PageHeader, useRowLink } from "../../components/page";
import { CreateDialogButton, DialogActions } from "../../components/Dialog";

const t = ko.workshop;

export function WorkshopPage() {
  const me = useMe();
  const isModeler = me.data?.roles.includes("modeler") ?? false;
  // 병합할 제출 인터뷰(모델러만, Phase 18b)
  const [picked, setPicked] = useState<number[]>([]);
  const toggle = (id: number) => setPicked((xs) => (xs.includes(id) ? xs.filter((x) => x !== id) : [...xs, id]));
  const sessions = useQuery({
    queryKey: ["workshop", "sessions"],
    queryFn: () => api.get<SessionSummary[]>("/workshop/sessions"),
  });

  const { rows: pagedRows, pager } = usePaged(sessions.data);
  const rowLink = useRowLink();
  return (
    <section className="stack">
      <PageHeader
        id="workshop"
        title={t.title}
        intro={t.intro}
        actions={
          <CreateDialogButton label={`+ ${t.newSession}`}>
            {(close) => <NewSessionForm onCancel={close} />}
          </CreateDialogButton>
        }
      />
      {sessions.isPending && <p>{ko.common.loading}</p>}
      {sessions.isError && <ErrorBox error={sessions.error} />}
      {sessions.data?.length === 0 && <EmptyState message={t.noSessions} />}
      {isModeler && picked.length > 0 && <MergePanel key={picked.join(",")} ids={picked} />}
      {sessions.data && sessions.data.length > 0 && (
        <>
        <table>
          <thead>
            <tr>
              {isModeler && <th title={ko.workshop.merge.pickHint}>{ko.workshop.merge.pick}</th>}
              <th className="num">id</th>
              <th>{t.sessionTitle}</th>
              <th>{ko.admin.status}</th>
              <th>{t.createdAt}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {pagedRows.map((s) => (
              <tr key={s.id} className="row-link" onClick={rowLink(`/workshop/${s.id}`)}>
                {isModeler && (
                  <td>
                    {s.status === "submitted" && (
                      <input
                        type="checkbox"
                        aria-label={ko.workshop.merge.pick}
                        checked={picked.includes(s.id)}
                        onChange={() => toggle(s.id)}
                      />
                    )}
                  </td>
                )}
                <td className="num">{s.id}</td>
                <td>{s.title ?? "—"}</td>
                <td>
                  <span className={`badge ws-${s.status}`}>{t.status[s.status]}</span>
                </td>
                <td>{formatDateTime(s.created_at)}</td>
                <td className="actions">
                  <Link to={`/workshop/${s.id}`}>{t.open}</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Pager pager={pager} />
        </>
      )}
    </section>
  );
}

/** 새 인터뷰 — 만들면 그 인터뷰 화면으로 간다. */
function NewSessionForm({ onCancel }: { onCancel: () => void }) {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<SessionDetail>("/workshop/sessions", { title: title.trim() || null }),
    onSuccess: (s) => navigate(`/workshop/${s.id}`),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <form className="stack" onSubmit={submit}>
      <label>
        {t.sessionTitle}
        <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      </label>
      {create.isError && <ErrorBox error={create.error} />}
      <DialogActions onCancel={onCancel} pending={create.isPending} submitLabel={t.newSession} />
    </form>
  );
}
