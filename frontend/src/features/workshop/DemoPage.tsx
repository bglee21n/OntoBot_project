import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../../api/client";
import type { DemoAnswerOut, DemoOut, DemoRowsOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { GradeBadge, QueryConditions } from "../chat/AnswerView";

const t = ko.workshop.demo;

/**
 * 샘플 데이터 시연(Phase 18c) — 게시 전 draft 에 샘플 행(CSV)을 넣고 운영과 같은 파이프라인으로 물어 본다.
 * 행은 그 버전의 격리 스키마에만 들어간다(운영 데이터와 섞이지 않음). 그래프가 없어 관계를 건너는 질문은 실패한다.
 */
export function DemoPage() {
  const id = Number(useParams().versionId);
  const qc = useQueryClient();
  const key = ["workshop", "demo", id];
  const status = useQuery({ queryKey: key, queryFn: () => api.get<DemoOut>(`/workshop/demo/${id}`) });
  const refresh = () => void qc.invalidateQueries({ queryKey: key });
  const drop = useMutation({ mutationFn: () => api.del(`/workshop/demo/${id}`), onSuccess: refresh });
  if (status.isError) return <ErrorBox error={status.error} />;
  if (!status.data) return <p>{ko.common.loading}</p>;
  const s = status.data;
  return (
    <section className="stack">
      <div className="row">
        <Link to="/workshop">{ko.workshop.back}</Link>
        <h1>{t.title(id)}</h1>
        <span className="spacer" />
        <Link to={`/ontology/versions/${id}`}>{t.openVersion}</Link>
      </div>
      <p className="muted">{t.intro}</p>
      {!s.ready && (
        <div className="card warn">
          <strong>{t.notReady}</strong>
          <ul>
            {s.issues.map((i) => (
              <li key={`${i.path}:${i.message}`}>
                <code>{i.path}</code> {i.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {s.ready && (
        <>
          {s.types.map((ty) => (
            <RowsForm key={ty.api_name} versionId={id} type={ty} onLoaded={refresh} />
          ))}
          {s.exists && <Ask versionId={id} />}
          {s.exists && (
            <button
              type="button"
              className="link"
              disabled={drop.isPending}
              onClick={() => window.confirm(t.confirmDrop) && drop.mutate()}
            >
              {t.drop}
            </button>
          )}
          {drop.isError && <ErrorBox error={drop.error} />}
        </>
      )}
    </section>
  );
}

function RowsForm({
  versionId,
  type,
  onLoaded,
}: {
  versionId: number;
  type: DemoOut["types"][number];
  onLoaded: () => void;
}) {
  const [csv, setCsv] = useState(type.columns.join(",") + "\n");
  const load = useMutation({
    mutationFn: () => api.post<DemoRowsOut>(`/workshop/demo/${versionId}/rows`, { object_type: type.api_name, csv }),
    onSuccess: onLoaded,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    load.mutate();
  };
  return (
    <form className="card stack" onSubmit={submit}>
      <div className="row">
        <strong>{type.display_name}</strong> <code>{type.api_name}</code>
        <span className="muted">{t.rows(type.rows)}</span>
      </div>
      <span className="muted">{t.csvHint(type.pk_property)}</span>
      <textarea
        className="plain"
        rows={4}
        aria-label={t.csv(type.display_name)}
        value={csv}
        onChange={(e) => setCsv(e.target.value)}
      />
      <div className="row">
        <button type="submit" disabled={load.isPending}>
          {t.load}
        </button>
        {load.data && <span className="muted">{t.loaded(load.data.changed, load.data.links)}</span>}
      </div>
      {load.isError && <ErrorBox error={load.error} />}
    </form>
  );
}

function Ask({ versionId }: { versionId: number }) {
  const [question, setQuestion] = useState("");
  const [answers, setAnswers] = useState<{ q: string; a: DemoAnswerOut }[]>([]);
  const ask = useMutation({
    mutationFn: (q: string) => api.post<DemoAnswerOut>(`/workshop/demo/${versionId}/ask`, { question: q }),
    onSuccess: (a, q) => {
      setAnswers((xs) => [{ q, a }, ...xs]);
      setQuestion("");
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (question.trim()) ask.mutate(question.trim());
  };
  return (
    <div className="card stack">
      <strong>{t.ask}</strong>
      <span className="muted">{t.askHint}</span>
      <form className="chat-input" onSubmit={submit}>
        <textarea value={question} placeholder={t.placeholder} onChange={(e) => setQuestion(e.target.value)} maxLength={2000} />
        <button type="submit" disabled={!question.trim() || ask.isPending}>
          {ask.isPending ? t.asking : t.send}
        </button>
      </form>
      {ask.isError && <ErrorBox error={ask.error} />}
      {answers.map(({ q, a }, i) => (
        <div key={i} className="stack demo-answer">
          <div className="bubble-user">{q}</div>
          {a.conditions && <QueryConditions text={a.conditions} graphAsOf={null} />}
          <div>{a.answer}</div>
          <div className="row">
            {a.grade != null && <GradeBadge grade={a.grade} />}
            {/* 근거는 대상마다 하나로(같은 대상의 여러 속성 인용) */}
            {[...new Map(a.citations.map((c) => [c.ref.slice(0, c.ref.lastIndexOf("#")), c.title])).entries()].map(
              ([target, title]) => (
                <span key={target} className="tag">
                  {title}
                </span>
              ),
            )}
            {a.failure_reason && <span className="muted">{t.failed(a.failure_reason)}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}
