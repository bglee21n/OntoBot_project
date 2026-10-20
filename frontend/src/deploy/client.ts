// 배포 콘솔 API(ADR-0014). 앱 API 가 아니라서 OpenAPI 생성 타입이 없다 — 모양은 deployer/src/ontobot_deployer/api.py 가 정본.
// 상태를 바꾸는 요청에는 X-Ontobot-Deploy 헤더를 붙인다(다른 사이트의 폼으로는 붙일 수 없다 — CSRF).
import { ApiError } from "../api/client";

export interface Me {
  id: number;
  username: string;
  display_name: string;
}

export interface Container {
  Service?: string;
  State?: string;
  Health?: string;
  Status?: string;
}

export interface Readyz {
  status: string;
  [k: string]: unknown;
}

export interface Status {
  ok: boolean;
  error?: string;
  current?: string;
  previous?: string;
  last_backup?: string;
  gpu?: boolean;
  free_mb?: number;
  readyz?: Readyz | null;
  containers?: Container[];
  deployer_next?: string;
  busy: boolean;
}

export interface Preflight {
  ok: boolean;
  version?: string;
  current?: string;
  app_only?: boolean;
  free_mb?: number;
  problems?: string[];
  warnings?: string[];
  error?: string;
}

export interface Bundle {
  name: string;
  version: string | null;
  commit: string | null;
  dirty: boolean;
  app_only: boolean;
  built_at: string | null;
  size_mb: number;
  location: "imported" | "drop";
  preflight: Preflight | null;
}

export type JobKind = "import" | "preflight" | "upgrade" | "rollback" | "backup" | "deployer_update";

export interface Job {
  id: string;
  kind: JobKind;
  params: Record<string, unknown>;
  actor: string;
  status: "running" | "succeeded" | "failed" | "not_ready" | "unknown";
  started_at: string;
  finished_at: string | null;
  steps: string[];
  result: Record<string, unknown> | null;
  exit_code: number | null;
  error: string | null;
}

export interface Backup {
  name: string;
  size_mb: number;
}

async function call<T>(method: string, path: string, body?: unknown, raw?: BodyInit, headers?: Record<string, string>): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      ...(method === "GET" ? {} : { "X-Ontobot-Deploy": "1" }),
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...headers,
    },
    body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
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

export const deploy = {
  me: () => call<Me>("GET", "/me"),
  login: (username: string, password: string) => call<Me>("POST", "/login", { username, password }),
  logout: () => call<void>("POST", "/logout"),
  status: () => call<Status>("GET", "/status"),
  bundles: () => call<Bundle[]>("GET", "/bundles"),
  removeBundle: (name: string) => call<void>("DELETE", `/bundles/${encodeURIComponent(name)}`),
  backups: () => call<Backup[]>("GET", "/backups"),
  jobs: () => call<Job[]>("GET", "/jobs"),
  job: (id: string) => call<Job>("GET", `/jobs/${id}`),
  log: (id: string, offset: number) => call<{ text: string; next: number }>("GET", `/jobs/${id}/log?offset=${offset}`),
  start: (body: Record<string, unknown>) => call<Job>("POST", "/jobs", body),
  newUpload: () => call<{ id: string; chunk_mb: number; max_mb: number }>("POST", "/uploads"),
  uploadSize: (id: string) => call<{ size: number }>("GET", `/uploads/${id}`),
  uploadChunk: (id: string, offset: number, chunk: Blob) =>
    call<{ size: number }>("PUT", `/uploads/${id}`, undefined, chunk, { "X-Offset": String(offset) }),
};

/** 큰 zip 을 조각으로 올린다. 끊기면 서버 크기를 물어 거기서부터 다시 보낸다. */
export async function uploadFile(file: File, onProgress: (sent: number) => void): Promise<string> {
  const { id, chunk_mb, max_mb } = await deploy.newUpload();
  if (file.size > max_mb * 1024 * 1024) {
    throw new ApiError({ title: `파일이 상한(${max_mb}MB)보다 큽니다`, status: 413 });
  }
  const chunk = chunk_mb * 1024 * 1024;
  let offset = 0;
  let failures = 0;
  while (offset < file.size) {
    try {
      const { size } = await deploy.uploadChunk(id, offset, file.slice(offset, offset + chunk));
      offset = size;
      failures = 0;
      onProgress(offset);
    } catch (e) {
      failures += 1;
      if (failures > 5) {
        throw e;
      }
      await new Promise((r) => setTimeout(r, 1000 * failures));
      offset = (await deploy.uploadSize(id)).size;
    }
  }
  return id;
}
