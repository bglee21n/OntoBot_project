import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError, api, describeError } from "../api/client";
import type { Me, Providers } from "../api/types";
import { ErrorBox } from "../components/ErrorBox";
import { ko } from "../i18n/ko";

const ME_KEY = ["auth", "me"];

/** 현재 사용자. 401 이면 null — 오류가 아니라 '로그인 안 됨' 상태다. */
export function useMe() {
  return useQuery({
    queryKey: ME_KEY,
    queryFn: async (): Promise<Me | null> => {
      try {
        return await api.get<Me>("/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          return null;
        }
        throw e;
      }
    },
    staleTime: 60_000,
  });
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) {
    return <p>{ko.common.loading}</p>;
  }
  if (me.isError) {
    return <ErrorBox error={me.error} />;
  }
  if (me.data === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}

export function LoginPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const providers = useQuery({
    queryKey: ["auth", "providers"],
    queryFn: () => api.get<Providers>("/auth/providers"),
  });
  const ssoFailed = new URLSearchParams(location.search).get("error") === "sso_failed";
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const login = useMutation({
    mutationFn: () => api.post<Me>("/auth/local/login", { username, password }),
    onSuccess: (me) => {
      qc.setQueryData(ME_KEY, me);
      navigate(from, { replace: true });
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };

  return (
    <main className="login">
      <h1>{ko.appTitle}</h1>
      {ssoFailed && <p className="error">{ko.login.ssoFailed}</p>}
      {providers.data?.oidc && (
        // 전체 페이지 이동이어야 한다 — IdP 로 갔다가 /auth/callback 으로 돌아오는 리다이렉트 흐름이다.
        <a className="button" href={`/api/v1/auth/login?return_to=${encodeURIComponent(from)}`}>
          {ko.login.sso}
        </a>
      )}
      {providers.data?.oidc && providers.data.local && <p className="muted center">{ko.login.or}</p>}
      {providers.data?.local !== false && (
      <form onSubmit={submit} className="card stack">
        <h2>{ko.login.title}</h2>
        <label>
          {ko.login.username}
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label>
          {ko.login.password}
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {login.isError && <ErrorBox error={login.error} />}
        <button type="submit" disabled={login.isPending}>
          {login.isPending ? ko.login.submitting : ko.login.submit}
        </button>
      </form>
      )}
    </main>
  );
}

export function useLogout() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: () => api.post<void>("/auth/logout"),
    onSettled: () => {
      qc.clear();
      navigate("/login", { replace: true });
    },
  });
}

export { describeError };
