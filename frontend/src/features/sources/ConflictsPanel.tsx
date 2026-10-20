import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { SyncConflictOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.conflicts;
type Filter = "live" | "acknowledged" | "cleared";

/**
 * 편집 충돌(TRD 6.3) — 편집 레이어가 이기는 동안 원천이 바뀐 속성. 조용히 넘기지 않도록 목록으로 드러내고,
 * 모델러가 확인하거나 편집을 해제한다(해제하면 원천 값으로 돌아간다).
 */
export function ConflictsPanel() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("live");
  const list = useQuery({
    queryKey: ["sync-conflicts", filter],
    queryFn: () =>
      api.get<SyncConflictOut[]>(filter === "live" ? "/sync-conflicts" : `/sync-conflicts?status=${filter}`),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["sync-conflicts"] });
  const acknowledge = useMutation({
    mutationFn: (id: number) => api.post(`/sync-conflicts/${id}/acknowledge`),
    onSuccess: refresh,
  });
  const clearEdit = useMutation({
    mutationFn: (editId: number) => api.del(`/object-edits/${editId}`),
    onSuccess: async () => {
      await refresh();
      await qc.invalidateQueries({ queryKey: ["object"] });
    },
  });
  const when = (v: string | null | undefined) => (v ? new Date(v).toLocaleString() : "—");

  return (
    <div className="card stack">
      <div className="row">
        <strong>{t.title}</strong>
        <span className="spacer" />
        {(Object.keys(t.filter) as Filter[]).map((k) => (
          <button key={k} type="button" className={filter === k ? "" : "link"} onClick={() => setFilter(k)}>
            {t.filter[k]}
          </button>
        ))}
      </div>
      <p className="muted">{t.hint}</p>
      {list.isError && <ErrorBox error={list.error} />}
      {acknowledge.isError && <ErrorBox error={acknowledge.error} />}
      {clearEdit.isError && <ErrorBox error={clearEdit.error} />}
      {list.data?.length === 0 && <p className="muted">{t.none}</p>}
      {list.data && list.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>{t.object}</th>
              <th>{t.property}</th>
              <th>{t.editValue}</th>
              <th>{t.baseValue}</th>
              <th>{t.newValue}</th>
              <th>{t.editedBy}</th>
              <th>{ko.admin.status}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {list.data.map((c) => (
              <tr key={c.id}>
                <td>
                  {c.type} · <code>{c.pk}</code>
                </td>
                <td>{c.property_display_name}</td>
                <td>
                  <strong>{c.edit_value}</strong>
                </td>
                <td>{c.base_source_value}</td>
                <td>
                  {c.new_source_value}
                  <div className="muted">{when(c.source_synced_at)}</div>
                </td>
                <td>
                  {c.edited_by ?? ko.chat.card.unknownUser}
                  <div className="muted">{when(c.edited_at)}</div>
                </td>
                <td>{t.status[c.status]}</td>
                <td>
                  {c.status === "open" && (
                    <button type="button" className="link" onClick={() => acknowledge.mutate(c.id)}>
                      {t.acknowledge}
                    </button>
                  )}
                  {c.status !== "cleared" && (
                    <button
                      type="button"
                      className="link danger"
                      onClick={() => window.confirm(t.clearConfirm) && clearEdit.mutate(c.edit_id)}
                    >
                      {t.clearEdit}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
