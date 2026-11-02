import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import type { EngagementDetail, EngagementSummary } from "../../api/types";
import { useMe } from "../../app/auth";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.adoption;

export function useEngagements(enabled = true) {
  return useQuery({
    queryKey: ["adoption", "engagements"],
    queryFn: () => api.get<EngagementSummary[]>("/adoption/engagements"),
    enabled,
  });
}

export function AdoptionPage() {
  const navigate = useNavigate();
  const me = useMe();
  const canCreate = me.data?.roles.some((r) => r === "modeler" || r === "admin") ?? false;
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const list = useEngagements();
  const create = useMutation({
    mutationFn: () =>
      api.post<EngagementDetail>("/adoption/engagements", {
        name: name.trim(),
        description: description.trim() || null,
      }),
    onSuccess: (e) => navigate(`/adoption/${e.id}`),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <section className="stack">
      <h1>{t.title}</h1>
      <p className="muted">{t.intro}</p>
      {canCreate && (
        <form className="row" onSubmit={submit}>
          <input aria-label={t.name} placeholder={t.name} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
          <input
            aria-label={t.description}
            placeholder={t.description}
            value={description}
            maxLength={4000}
            onChange={(e) => setDescription(e.target.value)}
          />
          <button type="submit" disabled={create.isPending || !name.trim()}>
            {t.create}
          </button>
        </form>
      )}
      {create.isError && <ErrorBox error={create.error} />}
      {list.isPending && <p>{ko.common.loading}</p>}
      {list.isError && <ErrorBox error={list.error} />}
      {list.data?.length === 0 && <p className="muted">{t.none}</p>}
      {list.data && list.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>id</th>
              <th>{t.name}</th>
              <th>{t.description}</th>
              <th>{t.myRole}</th>
              <th>{t.createdAt}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {list.data.map((e) => (
              <tr key={e.id}>
                <td>{e.id}</td>
                <td>{e.name}</td>
                <td className="muted">{e.description ?? "—"}</td>
                <td>{t.roles[e.my_role]}</td>
                <td>{new Date(e.created_at).toLocaleString("ko-KR")}</td>
                <td className="actions">
                  <Link to={`/adoption/${e.id}`}>{t.open}</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
