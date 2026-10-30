import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../../api/client";
import type { TraceOut, TraceSummaryOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { GradeBadge } from "../chat/AnswerView";

const t = ko.traces;
const STATUSES = ["ok", "empty", "failed", "not_query", "action_preview"] as const;
const PAGE = 50;

/**
 * 트레이스 화면(Phase 15, PRD 5.6 P1) — 질의 한 건이 질문 → 계획 → SQL/Cypher → 결과 수 → 근거 판정 →
 * 등급을 어떻게 지났는지. 트레이스 id 는 URL 에 둔다(객체 pk 가 아니다). 열람은 감사 로그에 남는다.
 */
export function TracesPage() {
  const params = useParams();
  const id = params.traceId ? Number(params.traceId) : null;
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [reason, setReason] = useState("");
  const [lookup, setLookup] = useState("");

  const list = useInfiniteQuery({
    queryKey: ["traces", status, reason],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => {
      const q = new URLSearchParams({ limit: String(PAGE) });
      if (status) q.set("status", status);
      if (reason) q.set("failure_reason", reason);
      if (pageParam !== null) q.set("before_id", String(pageParam));
      return api.get<TraceSummaryOut[]>(`/traces?${q}`);
    },
    getNextPageParam: (last) => (last.length === PAGE ? last.at(-1)?.id : undefined),
    refetchOnWindowFocus: false,
  });
  const rows = list.data?.pages.flat() ?? [];
  const go = (e: FormEvent) => {
    e.preventDefault();
    const n = Number(lookup);
    if (Number.isInteger(n) && n > 0) navigate(`/traces/${n}`);
  };

  return (
    <section className="stack">
      <h1>{t.title}</h1>
      <p className="muted">{t.intro}</p>
      <div className="traces">
        <div className="stack traces-list">
          <div className="row">
            <label className="row">
              {t.status}
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">{t.all}</option>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {t.statusName[s]}
                  </option>
                ))}
              </select>
            </label>
            <label className="row">
              {t.reason}
              <select value={reason} onChange={(e) => setReason(e.target.value)}>
                <option value="">{t.all}</option>
                {Object.entries(ko.chat.failureReason).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <span className="spacer" />
            <form className="row" onSubmit={go}>
              <input aria-label={t.lookup} placeholder={t.lookup} size={8} inputMode="numeric" value={lookup} onChange={(e) => setLookup(e.target.value)} />
              <button type="submit">{t.open}</button>
            </form>
          </div>
          {list.isError && <ErrorBox error={list.error} />}
          {list.isSuccess && rows.length === 0 && <p className="muted">{t.none}</p>}
          {rows.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>id</th>
                  <th>{t.question}</th>
                  <th>{t.result}</th>
                  <th>{t.rows}</th>
                  <th>{t.grounding}</th>
                  <th>{t.time}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className={r.id === id ? "selected-row" : ""}>
                    <td>
                      <Link to={`/traces/${r.id}`}>{r.id}</Link>
                    </td>
                    <td>
                      {r.question ?? "—"}
                      <div className="muted">
                        {r.user_name ?? "—"} · {new Date(r.created_at).toLocaleString("ko-KR")}
                      </div>
                    </td>
                    <td>
                      <Outcome status={r.status} reason={r.failure_reason} grade={r.grade} />
                    </td>
                    <td>{r.row_count ?? "—"}</td>
                    <td>
                      {r.fact_sentences ? t.verified(r.verified_sentences ?? 0, r.fact_sentences) : "—"}
                      {r.removed_citations > 0 && <div className="error-text">{t.removedCount(r.removed_citations)}</div>}
                    </td>
                    <td>{(r.total_ms / 1000).toFixed(1)}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {list.hasNextPage && (
            <button type="button" className="link" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
              {t.more}
            </button>
          )}
        </div>
        {id !== null && <TraceDetail id={id} />}
      </div>
    </section>
  );
}

function Outcome({ status, reason, grade }: { status: string | null; reason: string | null; grade: number | null }) {
  return (
    <span className="row">
      <span className={`badge ${status === "failed" ? "failed" : "succeeded"}`}>
        {status ? (t.statusName[status] ?? status) : "—"}
        {reason ? ` · ${ko.chat.failureReason[reason] ?? reason}` : ""}
      </span>
      {grade ? <GradeBadge grade={grade} /> : null}
    </span>
  );
}

interface PlanRecord {
  plan?: unknown;
  attempts?: unknown[];
}

/** 단계마다 한 칸 — 파이프라인 순서(chat/pipeline.py)와 같다. */
export function TraceDetail({ id }: { id: number }) {
  const q = useQuery({
    queryKey: ["traces", "detail", id],
    queryFn: () => api.get<TraceOut>(`/traces/${id}`),
    refetchOnWindowFocus: false,
  });
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return <p>{ko.common.loading}</p>;
  const tr = q.data;
  const plan = (tr.plan ?? {}) as PlanRecord;
  const attempts = plan.attempts ?? [];
  const ms = (k: string) => (tr.stage_latency_ms[k] != null ? ` · ${tr.stage_latency_ms[k]}ms` : "");
  return (
    <div className="card stack trace-detail">
      <div className="row">
        <strong>#{tr.id}</strong>
        <Outcome status={tr.status} reason={tr.failure_reason} grade={tr.grade} />
        <span className="spacer" />
        <span className="muted">{new Date(tr.created_at).toLocaleString("ko-KR")}</span>
      </div>
      <p className="muted">{t.masked}</p>
      <ol className="trace-steps">
        <li>
          <strong>{t.step.question}</strong>
          <div>{tr.question ?? t.noQuestion}</div>
        </li>
        <li>
          <strong>
            {t.step.plan}
            {ms("planning")}
          </strong>
          {attempts.length > 1 && <div className="notice warn">{t.replanned(attempts.length)}</div>}
          <pre>{JSON.stringify(plan.plan ?? null, null, 2)}</pre>
          {attempts.length > 0 && (
            <details>
              <summary>{t.attempts(attempts.length)}</summary>
              <pre>{JSON.stringify(attempts, null, 2)}</pre>
            </details>
          )}
        </li>
        <li>
          <strong>
            {t.step.query}
            {ms("querying")}
          </strong>
          {tr.sql_masked || tr.cypher_masked ? (
            <>
              {tr.cypher_masked && (
                <>
                  <div className="muted">Cypher (Neo4j)</div>
                  <pre>{tr.cypher_masked}</pre>
                </>
              )}
              {tr.sql_masked && (
                <>
                  <div className="muted">SQL (PostgreSQL)</div>
                  <pre>{tr.sql_masked}</pre>
                </>
              )}
              <div className="muted">{t.bound}</div>
            </>
          ) : (
            <div className="muted">{t.notRun}</div>
          )}
        </li>
        <li>
          <strong>{t.step.rows}</strong>
          <div>{tr.row_count != null ? t.rowCount(tr.row_count) : t.noRows}</div>
        </li>
        <li>
          <strong>
            {t.step.grounding}
            {ms("answering")}
            {ms("verifying")}
          </strong>
          <div>
            {tr.fact_sentences ? t.verified(tr.verified_sentences ?? 0, tr.fact_sentences) : t.noGrounding}
          </div>
          {tr.removed_citations.length > 0 && (
            <details open>
              <summary className="error-text">{t.removedCount(tr.removed_citations.length)}</summary>
              <pre>{JSON.stringify(tr.removed_citations, null, 2)}</pre>
            </details>
          )}
        </li>
        <li>
          <strong>{t.step.grade}</strong>
          <div>
            {tr.grade ? <GradeBadge grade={tr.grade} /> : t.noGrade}
            {tr.status === "failed" && <span className="muted"> {t.failedNote}</span>}
          </div>
        </li>
      </ol>
      <details>
        <summary>{t.llmCalls(tr.llm_calls.length)}</summary>
        <pre>{JSON.stringify(tr.llm_calls, null, 2)}</pre>
      </details>
    </div>
  );
}
