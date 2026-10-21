import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { ErrorBox } from "../components/ErrorBox";
import { JobTable, JobView, Light, type OpsJob, when } from "../components/ops/Jobs";
import { ko } from "../i18n/ko";
import { type BuiltBundle, type CheckLight, dev, type FileChange, jobApi, type TaskEntry } from "./client";

const t = ko.devconsole;
type Tab = keyof typeof t.tabs;

const mb = (n: number | null | undefined) => (n == null ? "—" : n >= 1024 ? `${(n / 1024).toFixed(1)} GB` : `${n} MB`);

function kindLabel(j: OpsJob): string {
  if (j.kind.startsWith("task:")) {
    return `task.py ${j.kind.slice(5)}`;
  }
  const name = typeof j.params.name === "string" ? ` ${j.params.name}` : "";
  return `${j.kind}${name}`;
}

/** 개발 콘솔(ADR-0015) — 개발 PC 의 127.0.0.1 전용. 명령의 정본은 scripts/task.py. */
export function DevConsoleApp() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [jobId, setJobId] = useState<string | null>(null);
  const ov = useQuery({ queryKey: ["dev", "overview"], queryFn: dev.overview, refetchInterval: 5_000 });
  const openJob = (job: OpsJob) => {
    setJobId(job.id);
    setTab("jobs");
    void qc.invalidateQueries({ queryKey: ["dev"] });
  };
  return (
    <div className="shell">
      <header className="topbar wrap">
        <strong>{t.title}</strong>
        <nav>
          {(Object.keys(t.tabs) as Tab[]).map((k) => (
            <button key={k} type="button" className={tab === k ? "" : "link"} onClick={() => { setTab(k); setJobId(null); }}>
              {t.tabs[k]}
            </button>
          ))}
        </nav>
        {ov.data?.busy && <span className="badge running">{t.busy}</span>}
        <span className="spacer" />
        <span className="muted">
          {ov.data?.git.branch ?? "—"} · {t.localOnly}
        </span>
      </header>
      <main className="content stack">
        {tab === "overview" && <OverviewPanel onJob={openJob} />}
        {tab === "source" && <SourcePanel onJob={openJob} />}
        {tab === "github" && <GitHubPanel />}
        {tab === "checks" && <TasksPanel group="check" onJob={openJob} />}
        {tab === "eval" && <TasksPanel group="eval" onJob={openJob} />}
        {tab === "build" && <BuildPanel onJob={openJob} />}
        {tab === "env" && <EnvPanel onJob={openJob} />}
        {tab === "jobs" &&
          (jobId ? (
            <JobView id={jobId} api={jobApi} kindLabel={kindLabel} onBack={() => setJobId(null)} extra={(j) => <JobResult result={j.result} />} />
          ) : (
            <JobList onOpen={(j) => setJobId(j.id)} />
          ))}
      </main>
    </div>
  );
}

function useStart(onJob: (j: OpsJob) => void) {
  return useMutation({ mutationFn: dev.start, onSuccess: onJob });
}

// ---------------------------------------------------------------- 결과 표시(테스트 요약·게이트)

function JobResult({ result }: { result: Record<string, unknown> | null }) {
  if (!result) {
    return null;
  }
  const r = result as Record<string, unknown> & {
    passed?: number;
    failed?: number;
    skipped?: number;
    failed_tests?: string[];
    gate_passed?: boolean | null;
    checks?: { metric: string; kind: string; op: string; limit: string; value: string; ok: boolean }[];
    failed_items?: string[];
    cassette_misses?: number | null;
  };
  return (
    <div className="stack">
      {r.passed !== undefined && (
        <span>
          <span className="ok-text">
            {t.passed} {r.passed}
          </span>
          {r.failed ? (
            <span className="error-text">
              {" "}
              · {t.failed} {r.failed}
            </span>
          ) : null}
          {r.skipped ? (
            <span className="muted">
              {" "}
              · {t.skipped} {r.skipped}
            </span>
          ) : null}
        </span>
      )}
      {(r.failed_tests ?? []).length > 0 && (
        <details open>
          <summary className="error-text">{t.failedTests}</summary>
          <ul>
            {r.failed_tests?.map((x) => (
              <li key={x}>
                <code>{x}</code>
              </li>
            ))}
          </ul>
        </details>
      )}
      {r.gate_passed != null && (
        <div>
          <strong>{t.gate}: </strong>
          <span className={r.gate_passed ? "ok-text" : "error-text"}>{r.gate_passed ? t.gatePassed : t.gateFailed}</span>
          {r.cassette_misses != null && (
            <span className="muted">
              {" "}
              · {t.misses} {r.cassette_misses}
            </span>
          )}
          {(r.failed_items ?? []).length > 0 && (
            <span className="muted">
              {" "}
              · {t.failedItems}: {r.failed_items?.join(", ")}
            </span>
          )}
          <table>
            <tbody>
              {r.checks?.map((c) => (
                <tr key={c.metric}>
                  <td className={c.ok ? "ok-text" : "error-text"}>{c.ok ? "✓" : "✕"}</td>
                  <td>{c.metric}</td>
                  <td className="muted">
                    {c.kind} {c.op} {c.limit}
                  </td>
                  <td>{c.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 대시보드

function CheckRow({ name, c }: { name: string; c: CheckLight }) {
  return (
    <tr>
      <td>
        <Light light={c.light}>{t.checkNames[name] ?? name}</Light>
      </td>
      <td>{c.detail}</td>
      <td className="muted">{c.job ? when(c.job.finished_at) : "—"}</td>
      <td>{c.job?.result ? <JobResult result={c.job.result} /> : null}</td>
    </tr>
  );
}

function OverviewPanel({ onJob }: { onJob: (j: OpsJob) => void }) {
  const ov = useQuery({ queryKey: ["dev", "overview"], queryFn: dev.overview, refetchInterval: 5_000 });
  const start = useStart(onJob);
  if (ov.isError) {
    return <ErrorBox error={ov.error} />;
  }
  if (!ov.data) {
    return <p>{ko.common.loading}</p>;
  }
  const { git, checks, warnings, signing, busy } = ov.data;
  return (
    <>
      <div className={`card ${git.dirty || warnings.length ? "warn" : "ok"}`}>
        <dl className="kv">
          <dt>{t.branch}</dt>
          <dd>
            <strong>{git.branch ?? "(detached)"}</strong>{" "}
            <span className="muted">{git.upstream ? t.ahead(git.ahead, git.behind) : t.noUpstream}</span>
          </dd>
          <dt>{t.head}</dt>
          <dd>
            <code>{git.head?.slice(0, 10) ?? "—"}</code>
          </dd>
          <dt>{t.ready}</dt>
          <dd className={git.dirty ? "warn-text" : "ok-text"}>{git.dirty ? t.dirtyCount(git.files.length) : t.clean}</dd>
          <dt>{t.signing}</dt>
          <dd className={signing.key_exists && signing.pub_exists ? "ok-text" : "warn-text"}>
            {signing.key_exists && signing.pub_exists ? t.signingOk : t.signingMissing}
          </dd>
        </dl>
      </div>
      <h2>{t.checks}</h2>
      <table>
        <tbody>
          {Object.entries(checks).map(([name, c]) => (
            <CheckRow key={name} name={name} c={c} />
          ))}
        </tbody>
      </table>
      <div className="row">
        <button type="button" disabled={busy || start.isPending} onClick={() => start.mutate({ kind: "check-all" })}>
          {t.checkAll}
        </button>
        <button type="button" disabled={busy || start.isPending || git.dirty} onClick={() => start.mutate({ kind: "release" })}>
          {t.release}
        </button>
        <span className="muted">{t.releaseHint}</span>
      </div>
      {start.isError && <ErrorBox error={start.error} />}
      <h2>{t.warnings}</h2>
      {warnings.length === 0 && <p className="muted">{t.noWarnings}</p>}
      {warnings.map((w) => (
        <div key={w.kind} className="notice">
          {w.text}
          {w.files && w.files.length > 0 && (
            <ul>
              {w.files.map((f) => (
                <li key={f}>
                  <code>{f}</code>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </>
  );
}

// ---------------------------------------------------------------- 소스

function fileState(f: FileChange): string {
  if (f.untracked) {
    return t.untracked;
  }
  const parts = [];
  if (f.index !== ".") {
    parts.push(`${t.staged} ${f.index}`);
  }
  if (f.worktree !== ".") {
    parts.push(`${t.working} ${f.worktree}`);
  }
  return parts.join(" · ");
}

function SourcePanel({ onJob }: { onJob: (j: OpsJob) => void }) {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["dev", "git", "status"], queryFn: dev.gitStatus, refetchInterval: 5_000 });
  const log = useQuery({ queryKey: ["dev", "git", "log"], queryFn: dev.gitLog });
  const br = useQuery({ queryKey: ["dev", "git", "branches"], queryFn: dev.gitBranches });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [diffOf, setDiffOf] = useState<{ path: string; staged: boolean } | null>(null);
  const [message, setMessage] = useState("");
  const [coauthor, setCoauthor] = useState(true);
  const [newBranch, setNewBranch] = useState("");
  const [tagName, setTagName] = useState("");
  const [tagMessage, setTagMessage] = useState("");
  const [pushTags, setPushTags] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["dev"] });
  const diff = useQuery({
    queryKey: ["dev", "git", "diff", diffOf?.path, diffOf?.staged],
    queryFn: () => dev.gitDiff(diffOf!.path, diffOf!.staged),
    enabled: diffOf !== null,
  });
  const commit = useMutation({
    mutationFn: () => dev.commit([...picked], message, coauthor),
    onSuccess: () => {
      setPicked(new Set());
      setMessage("");
      setDiffOf(null);
      void refresh();
    },
  });
  const branch = useMutation({ mutationFn: (name: string) => dev.createBranch(name), onSuccess: () => { setNewBranch(""); void refresh(); } });
  const sw = useMutation({ mutationFn: (name: string) => dev.switchBranch(name), onSuccess: refresh });
  const tag = useMutation({ mutationFn: () => dev.tag(tagName, tagMessage), onSuccess: () => { setTagName(""); setTagMessage(""); void refresh(); } });
  const push = useStart(onJob);
  const st = status.data;
  const files = st?.files ?? [];
  const toggle = (p: string) => {
    const next = new Set(picked);
    if (next.has(p)) {
      next.delete(p);
    } else {
      next.add(p);
    }
    setPicked(next);
  };

  return (
    <>
      {status.isError && <ErrorBox error={status.error} />}
      <div className="card stack">
        <div className="row">
          <strong>{t.changes}</strong>
          <span className="muted">{files.length}</span>
          <span className="spacer" />
          {files.length > 0 && (
            <button type="button" className="link" onClick={() => setPicked(new Set(files.map((f) => f.path)))}>
              {t.selectAll}
            </button>
          )}
        </div>
        {files.length === 0 && <p className="muted">{t.noChanges}</p>}
        <table>
          <tbody>
            {files.map((f) => (
              <tr key={f.path} className={diffOf?.path === f.path ? "selected-row" : ""}>
                <td>
                  <input type="checkbox" checked={picked.has(f.path)} onChange={() => toggle(f.path)} aria-label={f.path} />
                </td>
                <td>
                  <code>{f.path}</code>
                </td>
                <td className="muted">{fileState(f)}</td>
                <td className="actions">
                  {(f.worktree !== "." || f.untracked) && (
                    <button type="button" className="link" onClick={() => setDiffOf({ path: f.path, staged: false })}>
                      {t.diff}
                    </button>
                  )}
                  {f.index !== "." && !f.untracked && (
                    <button type="button" className="link" onClick={() => setDiffOf({ path: f.path, staged: true })}>
                      {t.stagedDiff}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {diffOf && (
          <div className="stack">
            <div className="row">
              <code>{diffOf.path}</code>
              <span className="spacer" />
              <button type="button" className="link" onClick={() => setDiffOf(null)}>
                {ko.common.cancel}
              </button>
            </div>
            {diff.isError && <ErrorBox error={diff.error} />}
            <DiffView text={diff.data?.diff ?? ko.common.loading} />
          </div>
        )}
        {files.length > 0 && (
          <form
            className="stack"
            onSubmit={(e) => {
              e.preventDefault();
              commit.mutate();
            }}
          >
            {st?.branch === "main" && <div className="notice">{t.commitOnMain}</div>}
            <label>
              {t.commitMessage}
              <textarea rows={4} value={message} onChange={(e) => setMessage(e.target.value)} />
            </label>
            <label className="check">
              <input type="checkbox" checked={coauthor} onChange={(e) => setCoauthor(e.target.checked)} /> {t.coauthor}
            </label>
            <div className="row">
              <button type="submit" disabled={picked.size === 0 || !message.trim() || commit.isPending}>
                {t.commit} ({picked.size})
              </button>
            </div>
            {commit.isError && <ErrorBox error={commit.error} />}
            {commit.data && <span className="ok-text">✓ {commit.data.commit}</span>}
          </form>
        )}
      </div>

      <div className="card stack">
        <div className="row">
          <strong>{t.branches}</strong>
          <span className="spacer" />
          <label className="check">
            <input type="checkbox" checked={pushTags} onChange={(e) => setPushTags(e.target.checked)} /> {t.pushTags}
          </label>
          <button type="button" disabled={push.isPending} onClick={() => push.mutate({ kind: "push", tags: pushTags })}>
            {t.push}
          </button>
        </div>
        {push.isError && <ErrorBox error={push.error} />}
        <table>
          <tbody>
            {br.data?.branches.map((b) => (
              <tr key={b.name}>
                <td>
                  <code>{b.name}</code> {b.current && <span className="badge succeeded">{t.current}</span>}
                </td>
                <td className="muted">
                  {b.short} · {when(b.date)}
                </td>
                <td className="actions">
                  {!b.current && (
                    <button type="button" className="link" disabled={sw.isPending} onClick={() => sw.mutate(b.name)}>
                      {t.switchTo}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            branch.mutate(newBranch.trim());
          }}
        >
          <input value={newBranch} onChange={(e) => setNewBranch(e.target.value)} placeholder={t.newBranch} style={{ minWidth: "18rem" }} />
          <button type="submit" disabled={!newBranch.trim() || branch.isPending}>
            {t.createSwitch}
          </button>
        </form>
        {(branch.isError || sw.isError) && <ErrorBox error={branch.error ?? sw.error} />}
        <span className="muted">{t.gitHint}</span>
      </div>

      <div className="card stack">
        <strong>{t.tags}</strong>
        <span className="muted">{br.data?.tags.slice(0, 8).join(" · ") || "—"}</span>
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            tag.mutate();
          }}
        >
          <input value={tagName} onChange={(e) => setTagName(e.target.value)} placeholder={t.newTag} />
          <input value={tagMessage} onChange={(e) => setTagMessage(e.target.value)} placeholder={t.tagMessage} style={{ minWidth: "18rem" }} />
          <button type="submit" disabled={!tagName.trim() || tag.isPending}>
            {t.createTag}
          </button>
        </form>
        {tag.isError && <ErrorBox error={tag.error} />}
      </div>

      <h2>{t.history}</h2>
      <table>
        <tbody>
          {log.data?.map((c) => (
            <tr key={c.hash}>
              <td>
                <code>{c.short}</code>
              </td>
              <td>
                {c.subject} {c.refs && <span className="muted">({c.refs})</span>}
              </td>
              <td className="muted">{c.author}</td>
              <td className="muted">{when(c.date)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function DiffView({ text }: { text: string }) {
  const lines = useMemo(() => text.split("\n"), [text]);
  return (
    <pre className="log diff">
      {lines.map((l, i) => (
        <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "diff-add" : l.startsWith("-") && !l.startsWith("---") ? "diff-del" : l.startsWith("@@") ? "diff-hunk" : ""}>
          {l || " "}
        </div>
      ))}
    </pre>
  );
}

// ---------------------------------------------------------------- GitHub

function GitHubPanel() {
  const qc = useQueryClient();
  const gh = useQuery({ queryKey: ["dev", "github"], queryFn: dev.github, refetchInterval: 30_000 });
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState(false);
  const pr = useMutation({
    mutationFn: () => dev.createPr(title, body, draft),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dev", "github"] }),
  });
  if (gh.isError) {
    return <ErrorBox error={gh.error} />;
  }
  if (!gh.data) {
    return <p>{ko.common.loading}</p>;
  }
  if (!gh.data.installed) {
    return <div className="notice">{t.ghMissing}</div>;
  }
  if (!gh.data.logged_in) {
    return <div className="notice">{t.ghLogin}</div>;
  }
  return (
    <>
      {gh.data.error && <div className="error">{gh.data.error}</div>}
      <h2>{t.prs}</h2>
      {gh.data.prs.length === 0 && <p className="muted">{t.noPrs}</p>}
      <table>
        <tbody>
          {gh.data.prs.map((p) => (
            <tr key={p.number}>
              <td>
                <a href={p.url} target="_blank" rel="noreferrer">
                  #{p.number}
                </a>
              </td>
              <td>
                {p.title} {p.isDraft && <span className="badge">draft</span>}
              </td>
              <td>
                <code>{p.headRefName}</code>
              </td>
              <td className={p.checks.failed ? "error-text" : "muted"}>{t.checksSummary(p.checks.total, p.checks.failed, p.checks.pending)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <form
        className="card stack"
        onSubmit={(e) => {
          e.preventDefault();
          pr.mutate();
        }}
      >
        <strong>{t.newPr}</strong>
        <label>
          {t.prTitle}
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          {t.prBody}
          <textarea rows={6} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={draft} onChange={(e) => setDraft(e.target.checked)} /> {t.draft}
        </label>
        <div>
          <button type="submit" disabled={!title.trim() || pr.isPending}>
            {t.createPr}
          </button>
        </div>
        {pr.isError && <ErrorBox error={pr.error} />}
        {pr.data && (
          <a href={pr.data.url} target="_blank" rel="noreferrer">
            {pr.data.url}
          </a>
        )}
      </form>
      <h2>{t.runs}</h2>
      {gh.data.runs.length === 0 && <p className="muted">{t.noRuns}</p>}
      <table>
        <tbody>
          {gh.data.runs.map((r) => (
            <tr key={r.databaseId}>
              <td className={r.conclusion === "success" ? "ok-text" : r.conclusion ? "error-text" : "muted"}>{r.conclusion ?? r.status}</td>
              <td>
                <a href={r.url} target="_blank" rel="noreferrer">
                  {r.displayTitle}
                </a>
              </td>
              <td className="muted">
                {r.workflowName} · {r.headBranch}
              </td>
              <td className="muted">{when(r.createdAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

// ---------------------------------------------------------------- 검사·평가(작업 단추)

function TasksPanel({ group, onJob }: { group: TaskEntry["group"]; onJob: (j: OpsJob) => void }) {
  const tasks = useQuery({ queryKey: ["dev", "tasks"], queryFn: dev.tasks });
  const ov = useQuery({ queryKey: ["dev", "overview"], queryFn: dev.overview, refetchInterval: 5_000 });
  const env = useQuery({ queryKey: ["dev", "env"], queryFn: dev.env, enabled: group === "eval" });
  const history = useQuery({ queryKey: ["dev", "jobs", group], queryFn: () => dev.jobs() });
  const start = useStart(onJob);
  const busy = !!ov.data?.busy;
  const mine = (tasks.data ?? []).filter((x) => x.group === group);
  const lastOf = (name: string) => history.data?.find((j) => j.kind === `task:${name}`);
  const cassetteWarn = ov.data?.warnings.find((w) => w.kind === "cassette");
  return (
    <>
      {group === "eval" && env.data && env.data.ollama.missing.length > 0 && (
        <div className="notice">
          {t.ollama}: {t.missing} {env.data.ollama.missing.join(", ")}
        </div>
      )}
      {group === "eval" && cassetteWarn && <div className="notice">{cassetteWarn.text}</div>}
      {group === "check" && (
        <div className="row">
          <button type="button" disabled={busy || start.isPending} onClick={() => start.mutate({ kind: "check-all" })}>
            {t.checkAll}
          </button>
        </div>
      )}
      {start.isError && <ErrorBox error={start.error} />}
      <table>
        <tbody>
          {mine.map((x) => {
            const last = lastOf(x.name);
            return (
              <tr key={x.name}>
                <td>
                  <strong>{x.label}</strong>
                  <div className="muted">
                    <code>task.py {x.name}</code> — {x.description}
                  </div>
                  {x.needs_model && <span className="badge">{t.modelNeeded}</span>}
                </td>
                <td>
                  {last ? (
                    <button type="button" className="link" onClick={() => onJob(last)}>
                      {ko.ops.jobStatus[last.status]} · {when(last.finished_at ?? last.started_at)}
                    </button>
                  ) : (
                    <span className="muted">—</span>
                  )}
                  {last?.result && <JobResult result={last.result} />}
                </td>
                <td className="actions">
                  <button type="button" disabled={busy || start.isPending} onClick={() => start.mutate({ kind: "task", name: x.name })}>
                    {t.run}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

// ---------------------------------------------------------------- 빌드·번들

function BuildPanel({ onJob }: { onJob: (j: OpsJob) => void }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["dev", "bundles"], queryFn: dev.bundles });
  const ov = useQuery({ queryKey: ["dev", "overview"], queryFn: dev.overview, refetchInterval: 5_000 });
  const tasks = useQuery({ queryKey: ["dev", "tasks"], queryFn: dev.tasks });
  const [appOnly, setAppOnly] = useState(true);
  const [zip, setZip] = useState(true);
  const [sign, setSign] = useState(true);
  const [target, setTarget] = useState<Record<string, string>>({});
  const start = useStart(onJob);
  const remove = useMutation({ mutationFn: dev.removeBundle, onSuccess: () => qc.invalidateQueries({ queryKey: ["dev", "bundles"] }) });
  const busy = !!ov.data?.busy;
  const dirty = !!ov.data?.git.dirty;
  const image = tasks.data?.find((x) => x.name === "image");
  return (
    <>
      <div className="card stack">
        <strong>{t.buildBundle}</strong>
        {dirty && <div className="notice">{t.dirtyWarn}</div>}
        {list.data && !list.data.signing_key_exists && <div className="notice">{t.signingMissing}</div>}
        <div className="row">
          <label className="check">
            <input type="radio" checked={appOnly} onChange={() => setAppOnly(true)} /> {t.appOnly}
          </label>
          <label className="check">
            <input type="radio" checked={!appOnly} onChange={() => setAppOnly(false)} /> {t.full}
          </label>
          <label className="check">
            <input type="checkbox" checked={zip} onChange={(e) => setZip(e.target.checked)} /> {t.withZip}
          </label>
          <label className="check">
            <input type="checkbox" checked={sign} onChange={(e) => setSign(e.target.checked)} /> {t.sign}
          </label>
          <button type="button" disabled={busy || start.isPending} onClick={() => start.mutate({ kind: "bundle-build", app_only: appOnly, zip, sign })}>
            {t.buildBundle}
          </button>
          {image && (
            <button type="button" className="link" disabled={busy || start.isPending} onClick={() => start.mutate({ kind: "task", name: "image" })}>
              {image.label}
            </button>
          )}
        </div>
        {start.isError && <ErrorBox error={start.error} />}
      </div>
      <h2>
        {t.bundles} <span className="muted">{list.data?.dir}</span>
      </h2>
      {list.isError && <ErrorBox error={list.error} />}
      {list.data?.bundles.length === 0 && <p className="muted">{t.noBundles}</p>}
      {list.data?.bundles.map((b: BuiltBundle) => (
        <div key={b.name} className="card stack">
          <div className="row">
            <strong>{b.version ?? b.name}</strong>
            <span className="badge">{b.app_only ? t.appOnly : t.full}</span>
            {b.dirty && <span className="badge failed">-dirty</span>}
            {b.signed ? <span className="badge succeeded">{t.signed}</span> : <span className="badge failed">{t.signed} ✕</span>}
            {b.verify && (
              <span className={b.verify.status === "succeeded" ? "ok-text" : "error-text"}>
                {t.verify}: {ko.ops.jobStatus[b.verify.status]}
              </span>
            )}
            <span className="spacer" />
            <span className="muted">
              {mb(b.size_mb)} · {when(b.built_at)} · <code>{b.commit}</code> {b.zip && "· zip"} {b.notes && `· ${t.notes}`}
            </span>
          </div>
          <div className="row">
            <button type="button" className="link" disabled={busy} onClick={() => start.mutate({ kind: "bundle-verify", name: b.name })}>
              {t.verify}
            </button>
            <button type="button" className="link" disabled={busy} onClick={() => start.mutate({ kind: "release-notes", name: b.name })}>
              {t.notes}
            </button>
            <input
              value={target[b.name] ?? ""}
              onChange={(e) => setTarget({ ...target, [b.name]: e.target.value })}
              placeholder={t.exportTarget}
              style={{ minWidth: "18rem" }}
            />
            <button
              type="button"
              className="link"
              disabled={busy || !(target[b.name] ?? "").trim()}
              onClick={() => start.mutate({ kind: "bundle-export", name: b.name, target: target[b.name] })}
            >
              {t.export}
            </button>
            <span className="spacer" />
            <button type="button" className="link danger" disabled={busy} onClick={() => window.confirm(t.removeConfirm) && remove.mutate(b.name)}>
              {t.remove}
            </button>
          </div>
        </div>
      ))}
      {remove.isError && <ErrorBox error={remove.error} />}
    </>
  );
}

// ---------------------------------------------------------------- 개발 환경

function ServiceLog({ name }: { name: string }) {
  const [text, setText] = useState("");
  const offset = useRef(0);
  useEffect(() => {
    let stop = false;
    async function pull() {
      const chunk = await dev.serviceLog(name, offset.current);
      if (!stop && chunk.text) {
        offset.current = chunk.next;
        setText((x) => (x + chunk.text).slice(-60_000));
      }
    }
    void pull();
    const timer = setInterval(() => void pull(), 1_500);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [name]);
  return <pre className="log">{text}</pre>;
}

function EnvPanel({ onJob }: { onJob: (j: OpsJob) => void }) {
  const qc = useQueryClient();
  const env = useQuery({ queryKey: ["dev", "env"], queryFn: dev.env, refetchInterval: 10_000 });
  const tasks = useQuery({ queryKey: ["dev", "tasks"], queryFn: dev.tasks });
  const [logOf, setLogOf] = useState<string | null>(null);
  const svc = useMutation({
    mutationFn: ({ name, op }: { name: string; op: "start" | "stop" | "restart" }) => dev.service(name, op),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dev", "env"] }),
  });
  const start = useStart(onJob);
  if (env.isError) {
    return <ErrorBox error={env.error} />;
  }
  const e = env.data;
  const envTasks = (tasks.data ?? []).filter((x) => x.group === "env");
  const section = (title: string, body: ReactNode) => (
    <div className="card stack">
      <strong>{title}</strong>
      {body}
    </div>
  );
  return (
    <>
      {section(
        t.services,
        <>
          <table>
            <tbody>
              {e?.services.map((s) => (
                <tr key={s.name}>
                  <td>
                    <code>task.py {s.name}</code>
                  </td>
                  <td className={s.running ? "ok-text" : "muted"}>
                    {s.running ? `${t.running} (pid ${s.pid})` : t.stopped}
                    {!s.running && s.exit_code != null && <span className="error-text"> exit {s.exit_code}</span>}
                  </td>
                  <td className="muted">
                    {s.port ? `${t.port} ${s.port}${s.port_open && !s.running ? ` — ${t.portBusy}` : ""}` : ""}
                  </td>
                  <td className="actions">
                    {!s.running && (
                      <button type="button" className="link" onClick={() => svc.mutate({ name: s.name, op: "start" })}>
                        {t.start}
                      </button>
                    )}
                    {s.running && (
                      <>
                        <button type="button" className="link" onClick={() => svc.mutate({ name: s.name, op: "restart" })}>
                          {t.restart}
                        </button>
                        <button type="button" className="link danger" onClick={() => svc.mutate({ name: s.name, op: "stop" })}>
                          {t.stop}
                        </button>
                      </>
                    )}
                    <button type="button" className="link" onClick={() => setLogOf(logOf === s.name ? null : s.name)}>
                      {t.serviceLog}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {svc.isError && <ErrorBox error={svc.error} />}
          {logOf && <ServiceLog key={logOf} name={logOf} />}
        </>,
      )}
      {section(
        t.containers,
        <>
          {e?.docker.projects == null ? (
            <span className="warn-text">{t.dockerDown}</span>
          ) : (
            <ul>
              {e.docker.projects.map((p) => (
                <li key={p.Name}>
                  <code>{p.Name}</code> <span className="muted">{p.Status}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="row">
            {envTasks.map((x) => (
              <button key={x.name} type="button" className="link" title={x.description} onClick={() => start.mutate({ kind: "task", name: x.name })}>
                {x.label}
              </button>
            ))}
          </div>
          {start.isError && <ErrorBox error={start.error} />}
        </>,
      )}
      {section(
        t.ollama,
        e && !e.ollama.reachable ? (
          <span className="warn-text">{t.ollamaDown}</span>
        ) : (
          <>
            <span>
              {t.required}:{" "}
              {e?.ollama.required.map((m) => (
                <code key={m} className={e.ollama.missing.includes(m) ? "error-text" : "ok-text"}>
                  {m}{" "}
                </code>
              ))}
            </span>
            <span className="muted">{e?.ollama.models.join(", ")}</span>
          </>
        ),
      )}
      {section(
        t.disk,
        <>
          <span>
            dist/bundle {mb(e?.disk.bundles_mb)} · .devconsole {mb(e?.disk.devconsole_mb)}
          </span>
          {e?.docker.disk && (
            <table>
              <tbody>
                {e.docker.disk.map((d) => (
                  <tr key={d.Type}>
                    <td>{d.Type}</td>
                    <td>{d.Size}</td>
                    <td className="muted">{d.Reclaimable}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>,
      )}
      {section(
        t.envKeys,
        e && e.env.missing.length > 0 ? (
          <span className="muted">
            {t.envMissing}: {e.env.missing.join(", ")}
          </span>
        ) : (
          <span className="ok-text">{t.envOk}</span>
        ),
      )}
    </>
  );
}

// ---------------------------------------------------------------- 작업 기록

function JobList({ onOpen }: { onOpen: (j: OpsJob) => void }) {
  const jobs = useQuery({ queryKey: ["dev", "jobs", "all"], queryFn: () => dev.jobs(), refetchInterval: 5_000 });
  if (jobs.isError) {
    return <ErrorBox error={jobs.error} />;
  }
  return <JobTable jobs={jobs.data ?? []} kindLabel={kindLabel} onOpen={onOpen} />;
}
