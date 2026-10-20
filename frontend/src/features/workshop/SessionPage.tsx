import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type KeyboardEvent, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../../api/client";
import type { ElementOut, PublishOut, SessionDetail, SessionSummary, TurnOut } from "../../api/types";
import { useMe } from "../../app/auth";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { ElementCard } from "./ElementCard";

const t = ko.workshop;
const KIND_ORDER = ["object_type", "property", "link_type", "action_candidate", "golden_query"];

export function SessionPage() {
  const id = Number(useParams().sessionId);
  const qc = useQueryClient();
  const me = useMe();
  const [text, setText] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [published, setPublished] = useState<PublishOut | null>(null);

  const sessionKey = ["workshop", "sessions", id];
  const elementsKey = ["workshop", "sessions", id, "elements"];
  const session = useQuery({
    queryKey: sessionKey,
    queryFn: () => api.get<SessionDetail>(`/workshop/sessions/${id}`),
  });
  const elements = useQuery({
    queryKey: elementsKey,
    queryFn: () => api.get<ElementOut[]>(`/workshop/sessions/${id}/elements`),
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: sessionKey });
    void qc.invalidateQueries({ queryKey: elementsKey });
  };

  const say = useMutation({
    mutationFn: (answer: string) =>
      api.post<TurnOut>(`/workshop/sessions/${id}/utterances`, {
        text: answer,
      }),
    onSuccess: (turn) => {
      setText("");
      setWarnings(turn.warnings);
      refresh();
    },
  });
  const submit = useMutation({
    mutationFn: () => api.post<SessionSummary>(`/workshop/sessions/${id}/submit`),
    onSuccess: refresh,
  });
  const publish = useMutation({
    mutationFn: () => api.post<PublishOut>(`/workshop/sessions/${id}/publish`),
    onSuccess: (out) => {
      setPublished(out);
      refresh();
    },
  });

  if (session.isPending) {
    return <p>{ko.common.loading}</p>;
  }
  if (session.isError) {
    return <ErrorBox error={session.error} />;
  }
  const s = session.data;
  const isInterviewee = me.data?.id === s.interviewee_id;
  const isModeler = me.data?.roles.includes("modeler") ?? false;
  const finished = s.current_topic === null;
  const canAnswer = s.status === "open" && !finished && isInterviewee;
  const editable = s.status !== "published" && (isInterviewee || isModeler);
  const els = elements.data ?? [];
  const unreviewed = els.filter((e) => e.status === "proposed").length;
  const seqOf = new Map(s.utterances.map((u) => [u.id, u.seq]));

  const send = (e?: FormEvent) => {
    e?.preventDefault();
    const answer = text.trim();
    if (answer && !say.isPending) {
      say.mutate(answer);
    }
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && e.ctrlKey) {
      send();
    }
  };

  return (
    <section className="stack">
      <div className="row">
        <Link to="/workshop">{t.back}</Link>
        <h1>{s.title ?? `${t.title} #${s.id}`}</h1>
        <span className={`badge ws-${s.status}`}>{t.status[s.status]}</span>
      </div>
      <ol className="topics">
        {s.topics.map((tp) => (
          <li key={tp.key} className={`topic topic-${tp.status}`}>
            {tp.title}
            <span className="muted"> · {t.topicStatus[tp.status]}</span>
          </li>
        ))}
      </ol>

      <div className="workshop">
        <div className="stack">
          <div className="chat-thread">
            {s.utterances.map((u) =>
              u.speaker === "agent" ? (
                <div key={u.id} className="bubble-agent">
                  {u.text}
                </div>
              ) : (
                <div key={u.id} className="bubble-user">
                  <span className="seq">#{u.seq}</span> {u.text}
                </div>
              ),
            )}
            {say.isPending && <p className="stage">{t.sending}</p>}
          </div>
          {warnings.map((w) => (
            <p key={w} className="notice">
              {t.warnings[w] ?? w}
            </p>
          ))}
          {say.isError && <ErrorBox error={say.error} />}
          {canAnswer && (
            <form className="chat-input" onSubmit={send}>
              <textarea
                value={text}
                placeholder={t.placeholder}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={onKey}
                maxLength={4000}
              />
              <button type="submit" disabled={!text.trim() || say.isPending}>
                {t.send}
              </button>
            </form>
          )}
          {s.status === "open" && finished && <p className="notice">{t.finished}</p>}
          {s.status === "open" && !finished && !isInterviewee && <p className="muted">{t.notInterviewee}</p>}
          {s.status !== "open" && <p className="muted">{t.closed}</p>}
        </div>

        <aside className="stack">
          <h2>{t.review}</h2>
          {elements.isError && <ErrorBox error={elements.error} />}
          {els.length === 0 && <p className="muted">{t.noElements}</p>}
          {KIND_ORDER.map((kind) => {
            const group = els.filter((e) => e.kind === kind);
            return (
              group.length > 0 && (
                <div key={kind} className="stack">
                  <h3>
                    {t.kinds[kind]} <span className="muted">({group.length})</span>
                  </h3>
                  {group.map((el) => (
                    <ElementCard
                      key={el.id}
                      element={el}
                      editable={editable}
                      seqOf={seqOf}
                      onChanged={() => void qc.invalidateQueries({ queryKey: elementsKey })}
                    />
                  ))}
                </div>
              )
            );
          })}

          {s.status === "open" && (isInterviewee || isModeler) && (
            <div className="card stack">
              <p className="muted">{t.submitHint}</p>
              <button
                type="button"
                disabled={unreviewed > 0 || els.length === 0 || submit.isPending}
                onClick={() => submit.mutate()}
              >
                {t.submit}
              </button>
              {submit.isError && <ErrorBox error={submit.error} />}
            </div>
          )}
          {s.status === "submitted" && isModeler && (
            <div className="card stack">
              <p className="muted">{t.publishHint}</p>
              <button type="button" disabled={publish.isPending} onClick={() => publish.mutate()}>
                {t.publish}
              </button>
              {publish.isError && <ErrorBox error={publish.error} />}
            </div>
          )}
          {(published || s.published_version_id) && (
            <div className="card ok stack">
              {published && <p>{t.published(published.version_id, published.golden_items)}</p>}
              <Link to={`/ontology/versions/${published?.version_id ?? s.published_version_id}`}>
                {t.openDraft}
              </Link>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
