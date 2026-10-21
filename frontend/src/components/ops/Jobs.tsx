// 작업 기록 보기 — 배포 콘솔(ADR-0014)과 개발 콘솔(ADR-0015)이 함께 쓴다. 서버 쪽은 opsjobs(같은 모양의 작업 기록).
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { ErrorBox } from "../ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.ops;

export interface OpsJob {
  id: string;
  kind: string;
  params: Record<string, unknown>;
  actor: string;
  status: "running" | "succeeded" | "failed" | "not_ready" | "unknown";
  started_at: string;
  finished_at: string | null;
  steps: string[];
  result: Record<string, unknown> | null;
  exit_code: number | null;
  error: string | null;
  meta?: Record<string, unknown>;
}

/** 콘솔마다 다른 API 경로를 넘긴다. */
export interface OpsJobApi {
  job: (id: string) => Promise<OpsJob>;
  log: (id: string, offset: number) => Promise<{ text: string; next: number }>;
  logUrl: (id: string) => string;
}

export const when = (v: string | null | undefined) => (v ? new Date(v).toLocaleString() : "—");

export function JobBadge({ status }: { status: OpsJob["status"] }) {
  const cls = status === "succeeded" ? "succeeded" : status === "running" ? "running" : "failed";
  return <span className={`badge ${cls}`}>{t.jobStatus[status] ?? status}</span>;
}

/** 작업 하나 — 단계와 출력(진행 중이면 1초마다 이어 읽는다). */
export function JobView({
  id,
  api,
  kindLabel,
  onBack,
  extra,
}: {
  id: string;
  api: OpsJobApi;
  kindLabel: (job: OpsJob) => string;
  onBack: () => void;
  extra?: (job: OpsJob) => ReactNode;
}) {
  const job = useQuery({
    queryKey: ["ops-job", id],
    queryFn: () => api.job(id),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1_000 : false),
  });
  const [log, setLog] = useState("");
  const offset = useRef(0);
  const running = job.data?.status === "running";
  const pre = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let stop = false;
    async function pull() {
      for (;;) {
        const chunk = await api.log(id, offset.current);
        if (stop) {
          return;
        }
        if (!chunk.text) {
          break;
        }
        offset.current = chunk.next;
        setLog((l) => l + chunk.text);
      }
    }
    void pull();
    const timer = running ? setInterval(() => void pull(), 1_000) : undefined;
    return () => {
      stop = true;
      if (timer) {
        clearInterval(timer);
      }
    };
  }, [id, running, api]);

  useEffect(() => {
    pre.current?.scrollTo({ top: pre.current.scrollHeight });
  }, [log]);

  if (job.isError) {
    return <ErrorBox error={job.error} />;
  }
  const j = job.data;
  return (
    <div className="stack">
      <button type="button" className="link" onClick={onBack} style={{ alignSelf: "flex-start" }}>
        {t.back}
      </button>
      {j && (
        <div className="card stack">
          <div className="row">
            <strong>{kindLabel(j)}</strong>
            <JobBadge status={j.status} />
            <span className="spacer" />
            <span className="muted">
              {t.actor} {j.actor} · {t.started} {when(j.started_at)} · {t.finished} {when(j.finished_at)}
            </span>
          </div>
          {j.error && <div className="error">{j.error}</div>}
          {extra?.(j)}
          {j.steps.length > 0 && (
            <div>
              <strong>{t.steps}</strong>
              <ol>
                {j.steps.map((s, i) => (
                  <li key={i} className={i === j.steps.length - 1 && running ? "running-step" : ""}>
                    {s}
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
      <div className="row">
        <strong>{t.log}</strong>
        <span className="spacer" />
        <a href={api.logUrl(id)}>{t.downloadLog}</a>
      </div>
      <pre ref={pre} className="log">
        {log}
      </pre>
    </div>
  );
}

/** 작업 목록 표. */
export function JobTable({ jobs, kindLabel, onOpen }: { jobs: OpsJob[]; kindLabel: (job: OpsJob) => string; onOpen: (j: OpsJob) => void }) {
  if (jobs.length === 0) {
    return <p className="muted">{t.noJobs}</p>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>{t.started}</th>
          <th>{t.kind}</th>
          <th>{t.state}</th>
          <th>{t.actor}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {jobs.map((j) => (
          <tr key={j.id}>
            <td>{when(j.started_at)}</td>
            <td>{kindLabel(j)}</td>
            <td>
              <JobBadge status={j.status} />
            </td>
            <td>{j.actor}</td>
            <td className="actions">
              <button type="button" className="link" onClick={() => onOpen(j)}>
                {t.open}
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** 신호등 하나. */
export function Light({ light, children }: { light: "ok" | "warn" | "fail" | "unknown"; children: ReactNode }) {
  const mark = { ok: "●", warn: "▲", fail: "✕", unknown: "○" }[light];
  const cls = { ok: "ok-text", warn: "warn-text", fail: "error-text", unknown: "muted" }[light];
  return (
    <span className={cls}>
      {mark} {children}
    </span>
  );
}
