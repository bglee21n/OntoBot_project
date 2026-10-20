import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { EvalRunDetail } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.eval;
const FLAGS = ["violation", "cover_up", "unanswerable_graded", "error"] as const;

function fmt(v: number | null | undefined): string {
  if (v === null || v === undefined) {
    return "—";
  }
  return Number.isInteger(v) ? String(v) : v.toFixed(4);
}

/** 실행 하나 — 게이트 판정과 문항별 결과. 틀린 문항은 왜 틀렸는지(빠진 값·조건)를 함께 보인다. */
export function RunView({ runId }: { runId: number }) {
  const run = useQuery({
    queryKey: ["eval-runs", "detail", runId],
    queryFn: () => api.get<EvalRunDetail>(`/eval-runs/${runId}`),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 3000 : false),
  });
  if (run.isPending) {
    return <p>{ko.common.loading}</p>;
  }
  if (run.isError) {
    return <ErrorBox error={run.error} />;
  }
  const r = run.data;
  return (
    <div className="card stack">
      <div className="row">
        <strong>#{r.id}</strong>
        <span className={`badge ${r.status}`}>{t.runStatus[r.status]}</span>
        {r.gate_passed !== null && (
          <span className={`badge ${r.gate_passed ? "succeeded" : "failed"}`}>
            {r.gate_passed ? t.gatePassed : t.gateFailed}
          </span>
        )}
        <span className="muted">
          {r.results} {t.items}
        </span>
      </div>

      {r.gate_checks.length > 0 && (
        <>
          <h3>{t.checks}</h3>
          <table>
            <thead>
              <tr>
                <th>{t.metric}</th>
                <th />
                <th>{t.limit}</th>
                <th>{t.value}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {r.gate_checks.map((c) => (
                <tr key={`${c.metric}-${c.op}`}>
                  <td>
                    <code>{c.metric}</code>
                  </td>
                  <td className="muted">{t.kind[c.kind]}</td>
                  <td>
                    {c.op === "min" ? "≥" : "≤"} {fmt(c.limit)}
                  </td>
                  <td>{fmt(c.value)}</td>
                  <td className={c.passed ? "ok-text" : "error-text"}>{c.passed ? "✓" : "✗"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3>{t.results}</h3>
      <table>
        <thead>
          <tr>
            <th>{t.key}</th>
            <th>{t.question}</th>
            <th>{t.expected}</th>
            <th>{t.actual}</th>
            <th>{t.correct}</th>
          </tr>
        </thead>
        <tbody>
          {r.result_list.map((x) => {
            const v = x.verdict as Record<string, unknown>;
            const misses = [
              ...((v.missing_values as string[] | undefined) ?? []).map((m) => `− ${m}`),
              ...((v.present_absent_values as string[] | undefined) ?? []).map((m) => `+ ${m}`),
              ...((v.missing_conditions as string[] | undefined) ?? []).map((m) => `조건 ${m}`),
            ];
            return (
              <tr key={x.id}>
                <td>{x.key ?? x.item_id}</td>
                <td title={x.answer ?? ""}>{x.question}</td>
                <td className="muted">{x.expected_status}</td>
                <td>
                  {x.status ?? "—"}
                  {x.failure_reason ? `/${x.failure_reason}` : ""}
                  {x.grade ? ` · ${"①②③"[x.grade - 1]}` : ""}
                  {FLAGS.filter((f) => v[f]).map((f) => (
                    <span key={f} className="badge failed flag">
                      {t.flags[f]}
                    </span>
                  ))}
                </td>
                <td className={v.correct ? "ok-text" : "error-text"}>
                  {v.correct ? "✓" : "✗"}
                  {misses.length > 0 && <div className="muted">{misses.join(", ")}</div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
