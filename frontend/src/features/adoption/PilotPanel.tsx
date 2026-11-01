// 36.14 파일럿 판단(TRD 17.17, Phase 22) — 근거(마지막 실제 모델 평가·성공 지표 목표·업무 지표)와 판단 기록.
// 고를 수 없는 판단은 단추를 끄고 까닭을 보인다. 서버가 같은 규칙으로 다시 본다(불변식 위반이면 422).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { DecisionOut, EngagementDetail, PilotOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { engagementKey } from "./queries";

const t = ko.adoption.pilot;
const DECISIONS = ["go", "go_after_fix", "no_go"] as const;

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : String(Math.round(v * 1000) / 1000));

export function PilotPanel({ e }: { e: EngagementDetail }) {
  const qc = useQueryClient();
  const key = ["adoption", "engagement", String(e.id), "pilot"];
  const pilot = useQuery({ queryKey: key, queryFn: () => api.get<PilotOut>(`/adoption/engagements/${e.id}/pilot`) });
  const [decision, setDecision] = useState<(typeof DECISIONS)[number]>("no_go");
  const [reason, setReason] = useState("");
  const decide = useMutation({
    mutationFn: () => api.post<DecisionOut>(`/adoption/engagements/${e.id}/decisions`, { decision, reason: reason.trim() }),
    onSuccess: async () => {
      setReason("");
      await qc.invalidateQueries({ queryKey: key });
      await qc.invalidateQueries({ queryKey: engagementKey(e.id), exact: true });
    },
  });
  if (pilot.isPending) return <p>{ko.common.loading}</p>;
  if (pilot.isError) return <ErrorBox error={pilot.error} />;
  const p = pilot.data;
  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    decide.mutate();
  };

  return (
    <section className="stack">
      <h2>{t.title}</h2>
      <div className="card stack">
        <strong>{t.run}</strong>
        {p.run ? (
          <>
            <p>
              <Link to={`/eval`}>#{p.run.run_id}</Link> · {p.run.golden_set} ·{" "}
              {p.run.gate_passed ? <span className="badge succeeded">{t.gatePassed}</span> : <span className="badge failed">{t.gateFailed}</span>}
              {p.run.invariant_violations.length > 0 && <span className="warn-text"> {t.violations(p.run.invariant_violations.join(", "))}</span>}
            </p>
            <table>
              <tbody>
                {p.run.checks.map((c) => (
                  <tr key={`${c.kind}-${c.metric}-${c.op}`}>
                    <td>{c.kind === "invariant" ? t.invariant : t.threshold}</td>
                    <td>{c.metric}</td>
                    <td>
                      {c.op === "min" ? "≥" : "≤"} {c.limit}
                    </td>
                    <td>{pct(c.value)}</td>
                    <td className={c.passed ? "ok-text" : "warn-text"}>{c.passed ? "✓" : "✗"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : (
          <p className="warn-text">{t.noRun}</p>
        )}
      </div>
      <div className="card stack">
        <strong>{t.targets}</strong>
        <table>
          <tbody>
            {p.targets.map((x) => (
              <tr key={x.key}>
                <td>{x.label}</td>
                <td>
                  {t.target} {x.direction === "min" ? "≥" : "≤"} {pct(x.target)}
                </td>
                <td>
                  {t.measured} {pct(x.measured)}
                </td>
                <td>{x.met === null ? "—" : x.met ? <span className="ok-text">✓</span> : <span className="warn-text">✗</span>}</td>
              </tr>
            ))}
            {p.business.map((b) => (
              <tr key={b.label}>
                <td>
                  {b.label} <span className="muted">({t.business})</span>
                </td>
                <td>
                  {t.target} {b.target ?? "—"}
                </td>
                <td>
                  {t.current} {b.current ?? "—"}
                </td>
                <td />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {e.can_decide && (
        <form className="card stack" onSubmit={submit}>
          <strong>{t.decide}</strong>
          <div className="row">
            {DECISIONS.map((d) => (
              <label key={d} title={p.allowed[d] ?? undefined} className={p.allowed[d] ? "muted" : ""}>
                <input type="radio" name="decision" value={d} checked={decision === d} disabled={Boolean(p.allowed[d])} onChange={() => setDecision(d)} />{" "}
                {t.decisions[d]}
              </label>
            ))}
          </div>
          {p.allowed.go && <p className="notice warn">{p.allowed.go}</p>}
          <textarea className="plain" aria-label={t.reason} placeholder={t.reason} rows={2} maxLength={4000} value={reason} onChange={(ev) => setReason(ev.target.value)} />
          <div>
            <button type="submit" disabled={decide.isPending || !reason.trim()}>
              {t.record}
            </button>
          </div>
          {decide.isError && <ErrorBox error={decide.error} />}
        </form>
      )}
      {p.decisions.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>{t.when}</th>
              <th>{t.decide}</th>
              <th>{t.reason}</th>
              <th>{t.run}</th>
            </tr>
          </thead>
          <tbody>
            {p.decisions.map((d) => (
              <tr key={d.id}>
                <td>{new Date(d.created_at).toLocaleString("ko-KR")}</td>
                <td>{t.decisions[d.decision]}</td>
                <td>{d.reason}</td>
                <td className="muted">{d.eval_run_id ? `#${d.eval_run_id}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {e.can_export && (
        <div className="card stack">
          <strong>{t.export}</strong>
          <p className="muted">{t.exportNote}</p>
          <div>
            <a className="button" href={`/api/v1/adoption/engagements/${e.id}/export`} download>
              {t.exportButton}
            </a>
          </div>
        </div>
      )}
    </section>
  );
}
