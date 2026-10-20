import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../../api/client";
import type { EvalRunDetail, EvalRunOut, GoldenSetDetail, GoldenSetOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { ItemsTable } from "./ItemsTable";
import { RunView } from "./RunView";

const t = ko.eval;

export function EvalPage() {
  const params = useParams();
  const setId = params.setId ? Number(params.setId) : null;
  const runId = params.runId ? Number(params.runId) : null;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [name, setName] = useState("");

  const sets = useQuery({
    queryKey: ["golden-sets"],
    queryFn: () => api.get<GoldenSetOut[]>("/golden-sets"),
  });
  const detail = useQuery({
    queryKey: ["golden-sets", setId],
    queryFn: () => api.get<GoldenSetDetail>(`/golden-sets/${setId}`),
    enabled: setId !== null,
  });
  const runs = useQuery({
    queryKey: ["eval-runs", setId],
    queryFn: () => api.get<EvalRunOut[]>(`/eval-runs?golden_set_id=${setId}`),
    enabled: setId !== null,
    // 실행 중이면 진행을 따라간다.
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "running") ? 3000 : false),
  });
  const create = useMutation({
    mutationFn: () => api.post<GoldenSetOut>("/golden-sets", { name: name.trim() }),
    onSuccess: (gs) => {
      setName("");
      void qc.invalidateQueries({ queryKey: ["golden-sets"] });
      navigate(`/eval/${gs.id}`);
    },
  });
  const start = useMutation({
    mutationFn: () => api.post<EvalRunDetail>("/eval-runs", { golden_set_id: setId }),
    onSuccess: (run) => {
      void qc.invalidateQueries({ queryKey: ["eval-runs", setId] });
      navigate(`/eval/${setId}/runs/${run.id}`);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) {
      create.mutate();
    }
  };

  return (
    <section className="stack">
      <h1>{t.title}</h1>
      <p className="muted">{t.intro}</p>
      <div className="eval">
        <aside className="stack">
          <h2>{t.sets}</h2>
          {sets.isError && <ErrorBox error={sets.error} />}
          {sets.data?.length === 0 && <p className="muted">{t.noSets}</p>}
          <nav className="chat-list">
            {sets.data?.map((gs) => (
              <Link key={gs.id} to={`/eval/${gs.id}`} className={gs.id === setId ? "active" : ""}>
                {gs.name} <span className="muted">({gs.items})</span>
              </Link>
            ))}
          </nav>
          <form className="stack" onSubmit={submit}>
            <input
              aria-label={t.setName}
              placeholder={t.newSet}
              value={name}
              maxLength={128}
              onChange={(e) => setName(e.target.value)}
            />
            <button type="submit" disabled={!name.trim() || create.isPending}>
              {t.create}
            </button>
          </form>
          {create.isError && <ErrorBox error={create.error} />}
        </aside>

        <div className="stack">
          {setId === null && <p className="muted">{t.sets} ←</p>}
          {detail.isError && <ErrorBox error={detail.error} />}
          {detail.data && (
            <>
              <div className="row">
                <h2>{detail.data.name}</h2>
                <span className="spacer" />
                <span className="muted">{t.runHint}</span>
                <button
                  type="button"
                  disabled={start.isPending || detail.data.items === 0}
                  onClick={() => start.mutate()}
                >
                  {t.run}
                </button>
              </div>
              {start.isError && <ErrorBox error={start.error} />}

              <h3>{t.runs}</h3>
              {runs.data?.length === 0 && <p className="muted">{t.noRuns}</p>}
              {runs.data && runs.data.length > 0 && (
                <table>
                  <thead>
                    <tr>
                      <th>id</th>
                      <th>{ko.admin.ts}</th>
                      <th>{t.model}</th>
                      <th>{ko.admin.status}</th>
                      <th>{t.accuracy}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {runs.data.map((r) => (
                      <tr key={r.id} className={r.id === runId ? "selected-row" : ""}>
                        <td>{r.id}</td>
                        <td>{new Date(r.started_at).toLocaleString("ko-KR")}</td>
                        <td>{modelOf(r)}</td>
                        <td>
                          <span className={`badge ${r.status}`}>{t.runStatus[r.status]}</span>{" "}
                          {r.gate_passed !== null && (
                            <span className={`badge ${r.gate_passed ? "succeeded" : "failed"}`}>
                              {r.gate_passed ? t.gatePassed : t.gateFailed}
                            </span>
                          )}
                        </td>
                        <td>{pct(r.metrics?.accuracy)}</td>
                        <td className="actions">
                          <Link to={`/eval/${setId}/runs/${r.id}`}>{t.open}</Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {runId !== null && <RunView runId={runId} />}

              <h3>
                {t.items} <span className="muted">({detail.data.items})</span>
              </h3>
              <ItemsTable set={detail.data} />
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function modelOf(r: EvalRunOut): string {
  const roles = (r.llm_config.roles ?? {}) as Record<string, { model?: string }>;
  const models = new Set(Object.values(roles).map((x) => x.model ?? "?"));
  const mode = typeof r.llm_config.mode === "string" && r.llm_config.mode !== "live" ? ` · ${r.llm_config.mode}` : "";
  return [...models].join(", ") + mode;
}

export function pct(v: unknown): string {
  return typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—";
}
