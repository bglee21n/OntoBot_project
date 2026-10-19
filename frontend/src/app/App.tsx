import { useQuery } from "@tanstack/react-query";

import { getJson } from "../api/client";
import { ko } from "../i18n/ko";

export function App() {
  const health = useQuery({
    queryKey: ["healthz"],
    queryFn: () => getJson<{ status: string }>("/healthz"),
    retry: false,
  });

  const status = health.isPending
    ? ko.health.checking
    : health.isError
      ? ko.health.down
      : ko.health.ok;

  return (
    <main>
      <h1>{ko.appTitle}</h1>
      <p role="status">{status}</p>
    </main>
  );
}
