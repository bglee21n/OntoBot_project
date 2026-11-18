import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import type { EngagementDetail, EngagementSummary } from "../../api/types";
import { useMe } from "../../app/auth";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { Pager, usePaged } from "../../components/Pager";
import { formatDateTime } from "../../app/format";
import { EmptyState, PageHeader } from "../../components/page";

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

  const { rows: pagedRows, pager } = usePaged(list.data);
  return (
    <section className="stack">
      <PageHeader id="adoption" title={t.title} intro={t.intro} />
      {canCreate && (
        <form className="row" onSubmit={submit}>
          <label>
            {t.name}
            <input value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            {t.description}
            <input
              value={description}
              maxLength={4000}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <button type="submit" disabled={create.isPending || !name.trim()}>
            {t.create}
          </button>
        </form>
      )}
      {create.isError && <ErrorBox error={create.error} />}
      {list.isPending && <p>{ko.common.loading}</p>}
      {list.isError && <ErrorBox error={list.error} />}
      {list.data?.length === 0 && <EmptyState message={t.none} />}
      {list.data && list.data.length > 0 && (
        <>
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
            {pagedRows.map((e) => (
              <tr key={e.id}>
                <td>{e.id}</td>
                <td>{e.name}</td>
                <td className="muted">{e.description ?? "—"}</td>
                <td>{t.roles[e.my_role]}</td>
                <td>{formatDateTime(e.created_at)}</td>
                <td className="actions">
                  <Link to={`/adoption/${e.id}`}>{t.open}</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Pager pager={pager} />
        </>
      )}
    </section>
  );
}
