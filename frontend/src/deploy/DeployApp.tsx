import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { ErrorBox } from "../components/ErrorBox";
import { ko } from "../i18n/ko";
import { type Bundle, deploy, type Job, type Status, uploadFile } from "./client";

const t = ko.deploy;
type Tab = "status" | "bundles" | "rollback" | "jobs";
const when = (v: string | null | undefined) => (v ? new Date(v).toLocaleString() : "—");

/** 배포 콘솔(ADR-0014) — 번들 올리기·사전 점검·업그레이드·롤백·백업. 절차는 서버의 ontobotctl 이 한다. */
export function DeployApp() {
  const me = useQuery({ queryKey: ["deploy", "me"], queryFn: deploy.me, retry: false });
  if (me.isPending) {
    return <p className="center">{ko.common.loading}</p>;
  }
  if (me.isError) {
    return <Login />;
  }
  return <Console name={me.data.display_name} />;
}

function Login() {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () => deploy.login(username, password),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["deploy"] }),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };
  return (
    <form className="login card" onSubmit={submit}>
      <h1>{t.title}</h1>
      <p className="muted">{t.loginHint}</p>
      <label>
        {ko.login.username}
        <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
      </label>
      <label>
        {ko.login.password}
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
      </label>
      <button type="submit" disabled={login.isPending}>
        {login.isPending ? ko.login.submitting : ko.login.submit}
      </button>
      {login.isError && <ErrorBox error={login.error} />}
    </form>
  );
}

function Console({ name }: { name: string }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("status");
  const [jobId, setJobId] = useState<string | null>(null);
  const status = useQuery({ queryKey: ["deploy", "status"], queryFn: deploy.status, refetchInterval: 10_000 });
  const logout = useMutation({ mutationFn: deploy.logout, onSuccess: () => qc.invalidateQueries({ queryKey: ["deploy"] }) });
  const openJob = (job: Job) => {
    setJobId(job.id);
    setTab("jobs");
    void qc.invalidateQueries({ queryKey: ["deploy"] });
  };
  return (
    <div className="shell">
      <header className="topbar">
        <strong>{t.title}</strong>
        <nav>
          {(Object.keys(t.tabs) as Tab[]).map((k) => (
            <button key={k} type="button" className={tab === k ? "" : "link"} onClick={() => { setTab(k); setJobId(null); }}>
              {t.tabs[k]}
            </button>
          ))}
        </nav>
        {status.data?.busy && <span className="badge running">{t.busy}</span>}
        <span className="spacer" />
        <span className="muted">{name}</span>
        <button type="button" className="link" onClick={() => logout.mutate()}>
          {ko.nav.logout}
        </button>
      </header>
      <main className="content stack">
        {tab === "status" && <StatusPanel status={status.data} error={status.error} onJob={openJob} />}
        {tab === "bundles" && <BundlesPanel current={status.data?.current} busy={!!status.data?.busy} onJob={openJob} />}
        {tab === "rollback" && <RollbackPanel status={status.data} onJob={openJob} />}
        {tab === "jobs" && (jobId ? <JobView id={jobId} onBack={() => setJobId(null)} /> : <JobList onOpen={(j) => setJobId(j.id)} />)}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------- 현황

function StatusPanel({ status, error, onJob }: { status: Status | undefined; error: unknown; onJob: (j: Job) => void }) {
  const backups = useQuery({ queryKey: ["deploy", "backups"], queryFn: deploy.backups });
  const start = useMutation({ mutationFn: deploy.start, onSuccess: onJob });
  if (error) {
    return <ErrorBox error={error} />;
  }
  if (!status) {
    return <p>{ko.common.loading}</p>;
  }
  if (!status.ok) {
    return <div className="error">{t.statusError}: {status.error}</div>;
  }
  const ready = status.readyz?.status ?? "unreachable";
  return (
    <>
      <div className={`card ${ready === "ok" ? "ok" : "warn"}`}>
        <dl className="kv">
          <dt>{t.current}</dt>
          <dd><strong>{status.current || t.none}</strong></dd>
          <dt>{t.previous}</dt>
          <dd>{status.previous || t.none}</dd>
          <dt>{t.readyz}</dt>
          <dd className={ready === "ok" ? "ok-text" : "error-text"}>{t.ready[ready] ?? ready}</dd>
          <dt>{t.lastBackup}</dt>
          <dd>{status.last_backup || t.none}</dd>
          <dt>{t.gpu}</dt>
          <dd>{status.gpu ? t.gpuYes : t.gpuNo}</dd>
          <dt>{t.freeDisk}</dt>
          <dd>{t.mb(status.free_mb ?? 0)}</dd>
        </dl>
      </div>
      <h2>{t.containers}</h2>
      <table>
        <thead>
          <tr>
            <th>{t.service}</th>
            <th>{t.state}</th>
            <th>{t.health}</th>
          </tr>
        </thead>
        <tbody>
          {(status.containers ?? []).map((c, i) => (
            <tr key={c.Service ?? i}>
              <td>{c.Service}</td>
              {/* migrate 는 마이그레이션 뒤 끝나는 일회성 컨테이너 — "Exited (0)" 이 정상이다. */}
              <td className={c.State === "running" || /^Exited \(0\)/.test(c.Status ?? "") ? "" : "error-text"}>{c.Status ?? c.State}</td>
              <td>{c.Health || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <button type="button" disabled={status.busy || start.isPending} onClick={() => start.mutate({ kind: "backup" })}>
          {t.backupNow}
        </button>
        {status.deployer_next && (
          <>
            <span className="muted">{t.deployerNext(status.deployer_next)}</span>
            <button
              type="button"
              disabled={status.busy || start.isPending}
              onClick={() => window.confirm(t.deployerUpdateConfirm) && start.mutate({ kind: "deployer_update" })}
            >
              {t.deployerUpdate}
            </button>
          </>
        )}
      </div>
      {start.isError && <ErrorBox error={start.error} />}
      <h2>{t.backups}</h2>
      {backups.data?.length === 0 && <p className="muted">{t.none}</p>}
      <ul>
        {backups.data?.map((b) => (
          <li key={b.name}>
            <code>{b.name}</code> <span className="muted">({t.mb(b.size_mb)})</span>
          </li>
        ))}
      </ul>
    </>
  );
}

// ---------------------------------------------------------------- 번들

function BundlesPanel({ current, busy, onJob }: { current: string | undefined; busy: boolean; onJob: (j: Job) => void }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["deploy", "bundles"], queryFn: deploy.bundles });
  const [sent, setSent] = useState<number | null>(null);
  const [upgrading, setUpgrading] = useState<Bundle | null>(null);
  const start = useMutation({ mutationFn: deploy.start, onSuccess: onJob });
  const remove = useMutation({
    mutationFn: deploy.removeBundle,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["deploy", "bundles"] }),
  });
  const upload = useMutation({
    mutationFn: async (file: File) => {
      setSent(0);
      const id = await uploadFile(file, (n) => setSent(Math.floor((n / file.size) * 100)));
      return deploy.start({ kind: "import", upload_id: id });
    },
    onSuccess: onJob,
    onSettled: () => setSent(null),
  });
  const imported = (list.data ?? []).filter((b) => b.location === "imported");
  const drop = (list.data ?? []).filter((b) => b.location === "drop");

  return (
    <>
      <div className="card stack">
        <strong>{t.upload}</strong>
        <span className="muted">{t.uploadHint}</span>
        <input
          type="file"
          accept=".zip"
          disabled={busy || upload.isPending}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) {
              upload.mutate(f);
            }
            e.target.value = "";
          }}
        />
        {sent !== null && <progress max={100} value={sent} aria-label={t.uploading(sent)} />}
        {sent !== null && <span>{t.uploading(sent)}</span>}
        {upload.isError && <ErrorBox error={upload.error} />}
      </div>
      {drop.length > 0 && (
        <div className="card stack">
          <strong>{t.dropTitle}</strong>
          <span className="muted">{t.dropHint}</span>
          {drop.map((b) => (
            <div key={b.name} className="row">
              <code>{b.name}</code>
              <span className="muted">{t.mb(b.size_mb)}</span>
              <button type="button" disabled={busy || start.isPending} onClick={() => start.mutate({ kind: "import", drop: b.name })}>
                {t.importIt}
              </button>
            </div>
          ))}
        </div>
      )}
      <h2>{t.imported}</h2>
      {list.isError && <ErrorBox error={list.error} />}
      {imported.length === 0 && <p className="muted">{t.noBundles}</p>}
      {imported.map((b) => (
        <div key={b.name} className={`card stack ${b.preflight?.ok ? "ok" : ""}`}>
          <div className="row">
            <strong>{b.version ?? b.name}</strong>
            <span className="badge">{b.app_only ? t.kindAppOnly : t.kindFull}</span>
            {b.dirty && <span className="badge failed">{t.dirty}</span>}
            {b.version === current && <span className="badge succeeded">{t.current}</span>}
            <span className="muted">
              {t.built} {when(b.built_at)} · {t.mb(b.size_mb)} · <code>{b.commit}</code>
            </span>
            <span className="spacer" />
            <button type="button" className="link" disabled={busy} onClick={() => start.mutate({ kind: "preflight", bundle: b.name })}>
              {t.runPreflight}
            </button>
            <button type="button" disabled={busy || !b.preflight?.ok} onClick={() => setUpgrading(b)}>
              {t.upgrade}
            </button>
            <button type="button" className="link danger" disabled={busy} onClick={() => window.confirm(t.removeConfirm) && remove.mutate(b.name)}>
              {t.remove}
            </button>
          </div>
          <PreflightView p={b.preflight} />
          {upgrading?.name === b.name && (
            <UpgradeForm bundle={b} current={current} onCancel={() => setUpgrading(null)} onJob={(j) => { setUpgrading(null); onJob(j); }} />
          )}
        </div>
      ))}
      {(start.isError || remove.isError) && <ErrorBox error={start.error ?? remove.error} />}
    </>
  );
}

function PreflightView({ p }: { p: Bundle["preflight"] }) {
  if (!p) {
    return <span className="muted">{t.preflight}: {t.preflightNone}</span>;
  }
  return (
    <div>
      <span className={p.ok ? "ok-text" : "error-text"}>
        {t.preflight}: {p.ok ? t.preflightOk : t.preflightFail}
      </span>
      {p.error && <div className="error-text">{p.error}</div>}
      {(p.problems ?? []).length > 0 && (
        <ul className="error-text">
          {p.problems?.map((x) => <li key={x}>{x}</li>)}
        </ul>
      )}
      {(p.warnings ?? []).length > 0 && (
        <ul className="muted">
          {p.warnings?.map((x) => <li key={x}>{t.warnings}: {x}</li>)}
        </ul>
      )}
    </div>
  );
}

function UpgradeForm({ bundle, current, onCancel, onJob }: { bundle: Bundle; current: string | undefined; onCancel: () => void; onJob: (j: Job) => void }) {
  const [typed, setTyped] = useState("");
  const start = useMutation({ mutationFn: deploy.start, onSuccess: onJob });
  const target = bundle.version ?? "";
  return (
    <form
      className="notice stack"
      onSubmit={(e) => {
        e.preventDefault();
        start.mutate({ kind: "upgrade", bundle: bundle.name, confirm_version: typed });
      }}
    >
      <strong>{t.upgradeTitle(current ?? "", target)}</strong>
      <span>{t.upgradeSteps}</span>
      <label>
        {t.typeVersion(target)}
        <input value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
      </label>
      <div className="row">
        <button type="submit" disabled={typed !== target || start.isPending}>
          {t.start}
        </button>
        <button type="button" className="link" onClick={onCancel}>
          {t.cancel}
        </button>
      </div>
      {start.isError && <ErrorBox error={start.error} />}
    </form>
  );
}

// ---------------------------------------------------------------- 롤백

function RollbackPanel({ status, onJob }: { status: Status | undefined; onJob: (j: Job) => void }) {
  const [restore, setRestore] = useState(false);
  const [typed, setTyped] = useState("");
  const start = useMutation({ mutationFn: deploy.start, onSuccess: onJob });
  if (!status?.previous) {
    return <p className="muted">{t.rollbackNone}</p>;
  }
  return (
    <form
      className="card stack"
      onSubmit={(e) => {
        e.preventDefault();
        start.mutate({ kind: "rollback", restore_db: restore, confirm: restore ? typed : null });
      }}
    >
      <strong>{t.rollbackTitle}</strong>
      <span>{t.rollbackTo(status.previous, status.current ?? "?")}</span>
      <label className="check">
        <input type="radio" checked={!restore} onChange={() => setRestore(false)} /> {t.rollbackApp}
      </label>
      <label className="check">
        <input type="radio" checked={restore} onChange={() => setRestore(true)} /> {t.rollbackDb}
      </label>
      {restore && (
        <div className="error stack">
          <span>{t.rollbackDbWarn}</span>
          <span>
            {t.lastBackup}: <code>{status.last_backup || t.none}</code>
          </span>
          <label>
            {t.typeRestore}
            <input value={typed} onChange={(e) => setTyped(e.target.value)} />
          </label>
        </div>
      )}
      <div>
        <button type="submit" className={restore ? "danger" : ""} disabled={status.busy || start.isPending || (restore && typed !== "RESTORE")}>
          {t.start}
        </button>
      </div>
      {start.isError && <ErrorBox error={start.error} />}
    </form>
  );
}

// ---------------------------------------------------------------- 작업 기록

function JobBadge({ job }: { job: Job }) {
  const cls = job.status === "succeeded" ? "succeeded" : job.status === "running" ? "running" : "failed";
  return <span className={`badge ${cls}`}>{t.jobStatus[job.status] ?? job.status}</span>;
}

function JobList({ onOpen }: { onOpen: (j: Job) => void }) {
  const jobs = useQuery({ queryKey: ["deploy", "jobs"], queryFn: deploy.jobs, refetchInterval: 5_000 });
  if (jobs.isError) {
    return <ErrorBox error={jobs.error} />;
  }
  if (jobs.data?.length === 0) {
    return <p className="muted">{t.noJobs}</p>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>{t.started}</th>
          <th>{t.tabs.jobs}</th>
          <th>{t.state}</th>
          <th>{t.actor}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {jobs.data?.map((j) => (
          <tr key={j.id}>
            <td>{when(j.started_at)}</td>
            <td>
              {t.kind[j.kind]} {typeof j.params.bundle === "string" && <code>{j.params.bundle}</code>}
            </td>
            <td>
              <JobBadge job={j} />
            </td>
            <td>{j.actor}</td>
            <td className="actions">
              <button type="button" className="link" onClick={() => onOpen(j)}>
                {ko.eval.open}
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function JobView({ id, onBack }: { id: string; onBack: () => void }) {
  const job = useQuery({
    queryKey: ["deploy", "job", id],
    queryFn: () => deploy.job(id),
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
        const chunk = await deploy.log(id, offset.current);
        if (stop) {
          return;
        }
        if (chunk.text) {
          offset.current = chunk.next;
          setLog((l) => l + chunk.text);
        }
        if (!chunk.text) {
          break;
        }
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
  }, [id, running]);

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
            <strong>{t.kind[j.kind]}</strong>
            {typeof j.params.bundle === "string" && <code>{j.params.bundle}</code>}
            <JobBadge job={j} />
            <span className="spacer" />
            <span className="muted">
              {t.actor} {j.actor} · {t.started} {when(j.started_at)} · {t.finished} {when(j.finished_at)}
            </span>
          </div>
          {j.error && <div className="error">{j.error}</div>}
          {j.status === "not_ready" && <div className="notice">{t.notReadyHint}</div>}
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
        </div>
      )}
      <div className="row">
        <strong>{t.log}</strong>
        <span className="spacer" />
        <a href={`/api/jobs/${id}/log.txt`}>{t.downloadLog}</a>
      </div>
      <pre ref={pre} className="log">
        {log}
      </pre>
    </div>
  );
}
