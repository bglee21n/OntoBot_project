// 개발 콘솔 API(ADR-0015). 앱 API 가 아니라 OpenAPI 생성 타입이 없다 — 모양은 devconsole/src/ontobot_devconsole/api.py 가 정본.
// 상태를 바꾸는 요청에는 X-Ontobot-Dev 헤더를 붙인다(다른 사이트가 이 로컬 콘솔을 부르지 못하게).
import { ApiError } from "../api/client";
import type { OpsJob, OpsJobApi } from "../components/ops/Jobs";

export type Light = "ok" | "warn" | "fail" | "unknown";

export interface FileChange {
  path: string;
  index: string;
  worktree: string;
  untracked: boolean;
}

export interface GitStatus {
  branch: string | null;
  upstream: string | null;
  ahead: number;
  behind: number;
  head: string | null;
  files: FileChange[];
  dirty: boolean;
}

export interface CheckLight {
  light: Light;
  detail: string;
  job: { id: string; status: string; finished_at: string | null; result: Record<string, unknown> | null } | null;
}

export interface Service {
  name: string;
  running: boolean;
  pid: number | null;
  uptime_s: number | null;
  exit_code: number | null;
  port: number | null;
  port_open: boolean | null;
}

export interface Overview {
  git: GitStatus;
  checks: Record<string, CheckLight>;
  warnings: { kind: string; text: string; files?: string[] }[];
  signing: { key_configured: boolean; key_exists: boolean; pub_exists: boolean };
  busy: boolean;
  services: Service[];
}

export interface TaskEntry {
  name: string;
  group: "check" | "eval" | "build" | "env";
  label: string;
  description: string;
  needs_model: boolean;
}

export interface Commit {
  hash: string;
  short: string;
  author: string;
  date: string;
  subject: string;
  refs: string;
}

export interface Branch {
  name: string;
  short: string;
  date: string;
  current: boolean;
}

export interface GitHubInfo {
  installed: boolean;
  logged_in: boolean;
  detail: string | null;
  error: string | null;
  prs: {
    number: number;
    title: string;
    headRefName: string;
    isDraft: boolean;
    url: string;
    updatedAt: string;
    checks: { total: number; failed: number; pending: number };
  }[];
  runs: {
    databaseId: number;
    displayTitle: string;
    headBranch: string;
    status: string;
    conclusion: string | null;
    url: string;
    createdAt: string;
    workflowName: string;
  }[];
}

export interface BuiltBundle {
  name: string;
  version: string | null;
  commit: string | null;
  dirty: boolean;
  app_only: boolean;
  built_at: string | null;
  size_mb: number;
  signed: boolean;
  zip: boolean;
  notes: boolean;
  verify: { status: string; finished_at: string | null } | null;
}

export interface EnvInfo {
  ollama: { reachable: boolean; models: string[]; required: string[]; missing: string[] };
  docker: { projects: { Name: string; Status: string; ConfigFiles?: string }[] | null; disk: { Type: string; Size: string; Reclaimable: string }[] | null };
  disk: { bundles_mb: number | null; devconsole_mb: number | null };
  env: { present: string[]; missing: string[] };
  services: Service[];
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      ...(method === "GET" ? {} : { "X-Ontobot-Dev": "1" }),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const b = (await res.json()) as { detail?: unknown };
      if (typeof b.detail === "string") {
        detail = b.detail;
      } else if (Array.isArray(b.detail)) {
        detail = "입력값을 확인하세요";
      }
    } catch {
      // 본문이 JSON 이 아니면 상태 줄만 쓴다.
    }
    throw new ApiError({ title: detail, status: res.status });
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const dev = {
  overview: () => call<Overview>("GET", "/overview"),
  tasks: () => call<TaskEntry[]>("GET", "/tasks"),
  jobs: (kind?: string) => call<OpsJob[]>("GET", `/jobs${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`),
  start: (body: Record<string, unknown>) => call<OpsJob>("POST", "/jobs", body),
  gitStatus: () => call<GitStatus>("GET", "/git/status"),
  gitLog: () => call<Commit[]>("GET", "/git/log?limit=30"),
  gitDiff: (path: string, staged: boolean) =>
    call<{ path: string; diff: string }>("GET", `/git/diff?path=${encodeURIComponent(path)}&staged=${staged}`),
  gitBranches: () => call<{ branches: Branch[]; tags: string[] }>("GET", "/git/branches"),
  createBranch: (name: string) => call<{ branch: string }>("POST", "/git/branch", { name, switch: true }),
  switchBranch: (name: string) => call<{ branch: string }>("POST", "/git/switch", { name, switch: true }),
  commit: (paths: string[], message: string, coauthor: boolean) => call<{ commit: string }>("POST", "/git/commit", { paths, message, coauthor }),
  tag: (name: string, message: string) => call<{ tag: string }>("POST", "/git/tag", { name, message }),
  github: () => call<GitHubInfo>("GET", "/github"),
  createPr: (title: string, body: string, draft: boolean) => call<{ url: string }>("POST", "/github/pr", { title, body, draft }),
  bundles: () => call<{ dir: string; bundles: BuiltBundle[]; signing_key: string | null; signing_key_exists: boolean }>("GET", "/bundles"),
  removeBundle: (name: string) => call<void>("DELETE", `/bundles/${encodeURIComponent(name)}`),
  env: () => call<EnvInfo>("GET", "/env"),
  service: (name: string, op: "start" | "stop" | "restart") => call<Service[]>("POST", `/services/${name}/${op}`),
  serviceLog: (name: string, offset: number) => call<{ text: string; next: number }>("GET", `/services/${name}/log?offset=${offset}`),
};

export const jobApi: OpsJobApi = {
  job: (id) => call<OpsJob>("GET", `/jobs/${id}`),
  log: (id, offset) => call<{ text: string; next: number }>("GET", `/jobs/${id}/log?offset=${offset}`),
  logUrl: (id) => `/api/jobs/${id}/log.txt`,
};
