// fetch 래퍼. API 타입은 scripts/export_openapi.py → openapi-typescript 로 생성한 schema.d.ts 를 쓴다
// (손으로 고치지 않는다 — TRD 16.4). 생성은 Phase 1에서 연결한다.
export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}
