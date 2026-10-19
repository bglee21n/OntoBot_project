import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";

import { api } from "../../api/client";
import type {
  Enqueued,
  GraphStatus,
  MappingIn,
  MappingOut,
  ObjectTypeOut,
  SourceIn,
  SourceOut,
  SyncRunOut,
  TestResult,
  VersionDetail,
  VersionSummary,
} from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.sources;

/** 현재 게시 버전의 정의 — 매핑은 게시 버전에만 맞춘다(서버도 같은 기준으로 검사한다). */
function usePublished() {
  return useQuery({
    queryKey: ["ontology", "published"],
    queryFn: async (): Promise<VersionDetail | null> => {
      const versions = await api.get<VersionSummary[]>("/ontology/versions");
      const pub = versions.find((v) => v.status === "published");
      return pub ? api.get<VersionDetail>(`/ontology/versions/${pub.id}`) : null;
    },
  });
}

export function SourcesPage() {
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.get<SourceOut[]>("/sources") });
  const mappings = useQuery({ queryKey: ["mappings"], queryFn: () => api.get<MappingOut[]>("/mappings") });
  const published = usePublished();

  return (
    <section className="stack">
      <h1>{t.title}</h1>
      <GraphPanel />

      <h2>{t.sources}</h2>
      {sources.isError && <ErrorBox error={sources.error} />}
      <SourceTable sources={sources.data ?? []} />
      <details className="card">
        <summary>{t.addSource}</summary>
        <SourceForm />
      </details>

      <h2>{t.mappings}</h2>
      {mappings.isError && <ErrorBox error={mappings.error} />}
      {(mappings.data ?? []).map((m) => (
        <MappingCard key={m.id} m={m} sources={sources.data ?? []} />
      ))}
      {published.data === null && <p className="notice">{t.noPublished}</p>}
      {published.data && (
        <details className="card">
          <summary>{t.addMapping}</summary>
          <MappingForm sources={sources.data ?? []} version={published.data} />
        </details>
      )}
    </section>
  );
}

function SourceTable({ sources }: { sources: SourceOut[] }) {
  const [results, setResults] = useState<Record<number, TestResult | Error>>({});
  const test = useMutation({
    mutationFn: (id: number) => api.post<TestResult>(`/sources/${id}/test`),
    onSuccess: (r, id) => setResults((prev) => ({ ...prev, [id]: r })),
    onError: (e, id) => setResults((prev) => ({ ...prev, [id]: e as Error })),
  });
  return (
    <table>
      <thead>
        <tr>
          <th>{t.name}</th>
          <th>{t.kind}</th>
          <th>{t.secretRef}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {sources.map((s) => {
          const r = results[s.id];
          return (
            <tr key={s.id}>
              <td>{s.name}</td>
              <td>{s.kind}</td>
              <td>
                <code>{s.secret_ref}</code>
              </td>
              <td className="actions">
                {r && !(r instanceof Error) && (
                  <span className={r.ok ? "ok-text" : "error-text"}>{r.ok ? `${t.testOk} · ${r.server}` : r.error}</span>
                )}
                {r instanceof Error && <span className="error-text">{r.message}</span>}{" "}
                <button type="button" className="link" onClick={() => test.mutate(s.id)}>
                  {t.test}
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function SourceForm() {
  const qc = useQueryClient();
  const [s, setS] = useState<SourceIn>({ kind: "postgres", name: "", secret_ref: "env:" });
  const create = useMutation({
    mutationFn: () => api.post<SourceOut>("/sources", s),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sources"] }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <form onSubmit={submit} className="grid-form">
      <label>
        {t.kind}
        <select value={s.kind} onChange={(e) => setS({ ...s, kind: e.target.value as SourceIn["kind"] })}>
          <option value="postgres">postgres</option>
        </select>
      </label>
      <label>
        {t.name}
        <input value={s.name} onChange={(e) => setS({ ...s, name: e.target.value })} required />
      </label>
      <label>
        {t.secretRef}
        <input value={s.secret_ref} onChange={(e) => setS({ ...s, secret_ref: e.target.value })} required />
      </label>
      <p className="muted span-all">{t.secretHint}</p>
      <div className="row">
        <button type="submit" disabled={create.isPending}>
          {ko.common.add}
        </button>
      </div>
      {create.isError && <ErrorBox error={create.error} />}
    </form>
  );
}

function MappingCard({ m, sources }: { m: MappingOut; sources: SourceOut[] }) {
  const qc = useQueryClient();
  const runs = useQuery({
    queryKey: ["sync-runs", m.id],
    queryFn: () => api.get<SyncRunOut[]>(`/sync-runs?mapping_id=${m.id}&limit=5`),
    refetchInterval: 5000,
  });
  const [notice, setNotice] = useState<string | null>(null);
  const sync = useMutation({
    mutationFn: () => api.post<Enqueued>(`/mappings/${m.id}/sync`),
    onSuccess: (r) => {
      setNotice(r.already_queued ? t.alreadyQueued : t.queued);
      void qc.invalidateQueries({ queryKey: ["mappings"] });
    },
  });
  const source = sources.find((s) => s.id === m.source_id);
  return (
    <article className="card stack">
      <div className="row">
        <h3>
          {m.object_type} ← <code>{m.table ?? "SELECT …"}</code>
        </h3>
        <span className="muted">
          {source?.name} · {m.mode === "full" ? t.full : t.incremental}
          {m.schedule ? ` · ${m.schedule}` : ""}
          {m.link_rules?.length ? ` · ${t.links}: ${m.link_rules.join(", ")}` : ""}
        </span>
        <span className="spacer" />
        {m.last_status && <span className={`badge ${m.last_status}`}>{t.status[m.last_status] ?? m.last_status}</span>}
        <button type="button" onClick={() => sync.mutate()} disabled={sync.isPending}>
          {t.syncNow}
        </button>
      </div>
      {notice && <p className="muted">{notice}</p>}
      {sync.isError && <ErrorBox error={sync.error} />}
      <table>
        <thead>
          <tr>
            <th>{t.runs}</th>
            <th>{t.lastStatus}</th>
            <th>{t.rows}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(runs.data ?? []).map((r) => (
            <tr key={r.id}>
              <td>{new Date(r.started_at).toLocaleString("ko-KR")}</td>
              <td>
                <span className={`badge ${r.status}`}>{t.status[r.status] ?? r.status}</span>
              </td>
              <td>
                {r.rows_read} / {r.rows_upserted} / {r.rows_deleted}
              </td>
              <td className="error-text">{r.error}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </article>
  );
}

function MappingForm({ sources, version }: { sources: SourceOut[]; version: VersionDetail }) {
  const qc = useQueryClient();
  const [sourceId, setSourceId] = useState<number | null>(sources[0]?.id ?? null);
  const [typeName, setTypeName] = useState(version.object_types[0]?.api_name ?? "");
  const [table, setTable] = useState("");
  const [columns, setColumns] = useState<string[]>([]);
  // 속성 → 원천 열. 화면은 속성 기준으로 고르고, 저장할 때 원천 열 → 속성으로 뒤집는다.
  const [byProp, setByProp] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<"full" | "incremental">("full");
  const [watermark, setWatermark] = useState("");
  const [links, setLinks] = useState<string[]>([]);
  const [schedule, setSchedule] = useState("");

  const type: ObjectTypeOut | undefined = version.object_types.find((x) => x.api_name === typeName);
  const linkChoices = useMemo(
    () => version.link_types.filter((l) => l.from_type === typeName && l.source.kind === "fk_property"),
    [version, typeName],
  );

  const load = useMutation({
    mutationFn: () => api.post<string[]>(`/sources/${sourceId}/columns`, { table }),
    onSuccess: (cols) => {
      setColumns(cols);
      // 이름이 정확히 같은 열만 미리 고른다 — 비슷한 이름을 짐작해 붙이지 않는다.
      const guess: Record<string, string> = {};
      for (const p of type?.properties ?? []) {
        if (cols.includes(p.api_name)) guess[p.api_name] = p.api_name;
      }
      setByProp(guess);
    },
  });

  const create = useMutation({
    mutationFn: () => {
      if (!type || sourceId === null) throw new Error("데이터 소스와 타입을 고르세요");
      const column_map = Object.fromEntries(
        Object.entries(byProp)
          .filter(([, col]) => col)
          .map(([prop, col]) => [col, prop]),
      );
      const body: MappingIn = {
        source_id: sourceId,
        object_type: type.api_name,
        table,
        column_map,
        pk_column: byProp[type.pk_property] ?? "",
        mode,
        watermark_column: mode === "incremental" ? watermark || null : null,
        link_rules: links,
        schedule: schedule || null,
      };
      return api.post<MappingOut>("/mappings", body);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["mappings"] }),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <form onSubmit={submit} className="stack">
      <div className="grid-form">
        <label>
          {t.source}
          <select value={sourceId ?? ""} onChange={(e) => setSourceId(Number(e.target.value))} required>
            {sources.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t.objectType}
          <select
            value={typeName}
            onChange={(e) => {
              setTypeName(e.target.value);
              setByProp({});
              setLinks([]);
            }}
          >
            {version.object_types.map((x) => (
              <option key={x.id} value={x.api_name}>
                {x.display_name} ({x.api_name})
              </option>
            ))}
          </select>
        </label>
        <label>
          {t.table}
          <input value={table} onChange={(e) => setTable(e.target.value)} placeholder="schema.table" required />
        </label>
        <div className="row">
          <button type="button" onClick={() => load.mutate()} disabled={!table || sourceId === null || load.isPending}>
            {t.loadColumns}
          </button>
        </div>
      </div>
      {load.isError && <ErrorBox error={load.error} />}

      {columns.length > 0 && type && (
        <>
          <table>
            <thead>
              <tr>
                <th>{ko.ontology.properties}</th>
                <th>{t.columnFor}</th>
              </tr>
            </thead>
            <tbody>
              {type.properties.map((p) => (
                <tr key={p.id}>
                  <td>
                    {p.display_name} <code>{p.api_name}</code>
                    {p.api_name === type.pk_property ? " (pk)" : ""}
                    {!p.nullable ? " *" : ""}
                  </td>
                  <td>
                    <select
                      value={byProp[p.api_name] ?? ""}
                      onChange={(e) => setByProp({ ...byProp, [p.api_name]: e.target.value })}
                    >
                      <option value="">{t.notMapped}</option>
                      {columns.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="grid-form">
            <label>
              {t.mode}
              <select value={mode} onChange={(e) => setMode(e.target.value as "full" | "incremental")}>
                <option value="full">{t.full}</option>
                <option value="incremental">{t.incremental}</option>
              </select>
            </label>
            {mode === "incremental" && (
              <label>
                {t.watermark}
                <select value={watermark} onChange={(e) => setWatermark(e.target.value)} required>
                  <option value="" />
                  {columns.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </label>
            )}
            <label>
              {t.schedule}
              <input value={schedule} onChange={(e) => setSchedule(e.target.value)} placeholder="every:15m" />
            </label>
            <p className="muted span-all">{t.scheduleHint}</p>
            {linkChoices.map((l) => (
              <label key={l.id} className="check">
                <input
                  type="checkbox"
                  checked={links.includes(l.api_name)}
                  onChange={(e) =>
                    setLinks(e.target.checked ? [...links, l.api_name] : links.filter((x) => x !== l.api_name))
                  }
                />
                {t.links}: {l.display_name} ({l.source.property} → {l.to_type})
              </label>
            ))}
          </div>
          <div className="row">
            <button type="submit" disabled={create.isPending}>
              {ko.common.save}
            </button>
          </div>
          {create.isError && <ErrorBox error={create.error} />}
        </>
      )}
    </form>
  );
}

function GraphPanel() {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["graph", "status"],
    queryFn: () => api.get<GraphStatus>("/graph/status"),
    refetchInterval: 5000,
  });
  const rebuild = useMutation({
    mutationFn: () => api.post("/graph/rebuild"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["graph", "status"] }),
  });
  const reconcile = useMutation({
    mutationFn: () => api.post("/graph/reconcile"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["graph", "status"] }),
  });
  const g = ko.graph;
  const s = status.data;
  const report = s?.last_reconcile as
    | { ok: boolean; stub_nodes: number; rows: { kind: string; name: string; pg_count: number; graph_count: number; ok: boolean }[] }
    | null
    | undefined;
  const bad = (report?.rows ?? []).filter((r) => !r.ok);

  return (
    <div className="card stack">
      <div className="row">
        <strong>{g.title}</strong>
        <span className="spacer" />
        <button type="button" className="link" onClick={() => reconcile.mutate()}>
          {g.reconcile}
        </button>
        <button
          type="button"
          className="link danger"
          onClick={() => window.confirm(g.rebuildConfirm) && rebuild.mutate()}
        >
          {g.rebuild}
        </button>
      </div>
      {status.isError && <ErrorBox error={status.error} />}
      {s && (
        <>
          <p>
            {s.neo4j_available ? `${g.available}: Neo4j ${s.neo4j_version}` : <span className="error-text">{g.unavailable}</span>}
            {" · "}
            {g.pending}: {s.pending_events} · {g.lag}: {g.seconds(s.projection_lag_seconds)}
          </p>
          {s.lag_warning && <p className="notice">{g.lagWarn}</p>}
          {report && (
            <p className="muted">
              {g.lastReconcile}: {s.last_reconcile_at ? new Date(s.last_reconcile_at).toLocaleString("ko-KR") : ""} ·{" "}
              {report.ok ? "일치" : `${g.mismatch} ${bad.length}건`}
              {report.stub_nodes > 0 ? ` · ${g.stubs} ${report.stub_nodes}건` : ""}
            </p>
          )}
          {bad.length > 0 && (
            <ul>
              {bad.map((r) => (
                <li key={`${r.kind}:${r.name}`}>
                  <code>{r.name}</code> PostgreSQL {r.pg_count} · Neo4j {r.graph_count}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {(rebuild.isError || reconcile.isError) && <ErrorBox error={rebuild.error ?? reconcile.error} />}
    </div>
  );
}
