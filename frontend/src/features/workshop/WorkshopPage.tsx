import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import type { SessionDetail, SessionSummary } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { useMe } from "../../app/auth";
import { ko } from "../../i18n/ko";
import { MergePanel } from "./MergePanel";

const t = ko.workshop;

export function WorkshopPage() {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const me = useMe();
  const isModeler = me.data?.roles.includes("modeler") ?? false;
  // 병합할 제출 인터뷰(모델러만, Phase 18b)
  const [picked, setPicked] = useState<number[]>([]);
  const toggle = (id: number) => setPicked((xs) => (xs.includes(id) ? xs.filter((x) => x !== id) : [...xs, id]));
  const sessions = useQuery({
    queryKey: ["workshop", "sessions"],
    queryFn: () => api.get<SessionSummary[]>("/workshop/sessions"),
  });
  const create = useMutation({
    mutationFn: () =>
      api.post<SessionDetail>("/workshop/sessions", {
        title: title.trim() || null,
      }),
    onSuccess: (s) => navigate(`/workshop/${s.id}`),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <section className="stack">
      <h1>{t.title}</h1>
      <p className="muted">{t.intro}</p>
      <form className="row" onSubmit={submit}>
        <input
          aria-label={t.sessionTitle}
          placeholder={t.sessionTitle}
          value={title}
          maxLength={200}
          onChange={(e) => setTitle(e.target.value)}
        />
        <button type="submit" disabled={create.isPending}>
          {t.newSession}
        </button>
      </form>
      {create.isError && <ErrorBox error={create.error} />}
      {sessions.isPending && <p>{ko.common.loading}</p>}
      {sessions.isError && <ErrorBox error={sessions.error} />}
      {sessions.data?.length === 0 && <p className="muted">{t.noSessions}</p>}
      {isModeler && picked.length > 0 && <MergePanel key={picked.join(",")} ids={picked} />}
      {sessions.data && sessions.data.length > 0 && (
        <table>
          <thead>
            <tr>
              {isModeler && <th title={ko.workshop.merge.pickHint}>{ko.workshop.merge.pick}</th>}
              <th>id</th>
              <th>{t.sessionTitle}</th>
              <th>{ko.admin.status}</th>
              <th>{t.createdAt}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sessions.data.map((s) => (
              <tr key={s.id}>
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
                <td>{s.id}</td>
                <td>{s.title ?? "—"}</td>
                <td>
                  <span className={`badge ws-${s.status}`}>{t.status[s.status]}</span>
                </td>
                <td>{new Date(s.created_at).toLocaleString("ko-KR")}</td>
                <td className="actions">
                  <Link to={`/workshop/${s.id}`}>{t.open}</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
