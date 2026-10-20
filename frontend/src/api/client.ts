// API 호출 래퍼. 오류 응답은 RFC 7807(problem+json)이다(TRD 14.1).
// 세션은 HttpOnly 쿠키라 스크립트가 읽지 않는다 — 같은 오리진 요청이면 브라우저가 붙인다.

export const API = "/api/v1";

export interface Problem {
  title: string;
  status: number;
  code?: string;
  detail?: string;
  issues?: { path: string; message: string }[];
  errors?: { loc: (string | number)[]; msg: string }[];
}

export class ApiError extends Error {
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.title);
    this.problem = problem;
  }

  get status(): number {
    return this.problem.status;
  }
}

async function toError(res: Response): Promise<ApiError> {
  let problem: Problem = { title: `${res.status} ${res.statusText}`, status: res.status };
  try {
    const body = (await res.json()) as Partial<Problem>;
    if (body && typeof body.title === "string") {
      problem = { ...problem, ...body } as Problem;
    }
  } catch {
    // 본문이 JSON 이 아니면 상태 줄만 쓴다.
  }
  return new ApiError(problem);
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path.startsWith("/") ? path : `${API}/${path}`, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    throw await toError(res);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? await res.json() : await res.text()) as T;
}

export const getJson = <T,>(path: string) => request<T>("GET", path);
export const api = {
  get: <T,>(path: string) => request<T>("GET", `${API}${path}`),
  post: <T,>(path: string, body?: unknown) => request<T>("POST", `${API}${path}`, body ?? {}),
  patch: <T,>(path: string, body: unknown) => request<T>("PATCH", `${API}${path}`, body),
  put: <T,>(path: string, body: unknown) => request<T>("PUT", `${API}${path}`, body),
  del: (path: string) => request<void>("DELETE", `${API}${path}`),
  text: (path: string) => request<string>("GET", `${API}${path}`),
};

/** 화면에 보여 줄 한 줄 + 세부 목록. 검증 오류·게시 전 검사 결과를 같은 모양으로 낸다. */
export function describeError(err: unknown): { title: string; items: string[] } {
  if (err instanceof ApiError) {
    const p = err.problem;
    const items = [
      ...(p.issues ?? []).map((i) => `${i.path}: ${i.message}`),
      ...(p.errors ?? []).map((e) => `${e.loc.join(".")}: ${e.msg}`),
    ];
    return { title: p.detail ? `${p.title} — ${p.detail}` : p.title, items };
  }
  return { title: err instanceof Error ? err.message : String(err), items: [] };
}
