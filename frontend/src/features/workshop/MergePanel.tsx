import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { MergeCandidatesOut, PublishOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.workshop.merge;

/**
 * 인터뷰 병합(Phase 18b) — 고른 제출 인터뷰들의 확정 요소를 이름 **정확 일치**로 묶어 보이고, 모델러가 draft 하나로
 * 병합한다. 충돌(같은 이름·다른 정의)은 병합을 막는다 — 각 인터뷰 검토에서 고치거나 거절한 뒤 다시.
 */
export function MergePanel({ ids }: { ids: number[] }) {
  const qc = useQueryClient();
  const look = useMutation({
    mutationFn: () => api.post<MergeCandidatesOut>("/workshop/merge/candidates", { session_ids: ids }),
  });
  const merge = useMutation({
    mutationFn: () => api.post<PublishOut>("/workshop/merge", { session_ids: ids }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["workshop", "sessions"] }),
  });
  const data = look.data;
  const conflicts = data?.groups.filter((g) => g.state === "conflict").length ?? 0;
  return (
    <div className="card stack">
      <strong>{t.title(ids.length)}</strong>
      <p className="muted">{t.hint}</p>
      <div className="row">
        <button type="button" disabled={ids.length < 2 || look.isPending} onClick={() => look.mutate()}>
          {t.look}
        </button>
        <button
          type="button"
          disabled={!data || conflicts > 0 || merge.isPending || merge.isSuccess}
          title={conflicts > 0 ? t.blocked : undefined}
          onClick={() => window.confirm(t.confirm(ids.length)) && merge.mutate()}
        >
          {t.merge}
        </button>
      </div>
      {(look.isError || merge.isError) && <ErrorBox error={look.error ?? merge.error} />}
      {merge.data && (
        <p className="notice">
          {t.done(merge.data.version_id, merge.data.golden_items)}{" "}
          <Link to={`/ontology/versions/${merge.data.version_id}`}>{ko.workshop.openDraft}</Link>{" "}
          <Link to={`/workshop/demo/${merge.data.version_id}`}>{ko.workshop.demo.open}</Link>
        </p>
      )}
      {data && (
        <>
          {data.same_display.length > 0 && (
            <div className="notice warn">
              <strong>{t.sameDisplay}</strong>
              <ul>
                {data.same_display.map((s) => (
                  <li key={`${s.kind}:${s.display_name}`}>
                    “{s.display_name}” — {s.keys.map((k) => <code key={k}>{k} </code>)}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <table>
            <thead>
              <tr>
                <th>{t.kind}</th>
                <th>{t.name}</th>
                <th>{t.state}</th>
                <th>{t.sessions}</th>
              </tr>
            </thead>
            <tbody>
              {data.groups.map((g) => (
                <tr key={`${g.kind}:${g.key}`} className={g.state === "conflict" ? "error-text" : undefined}>
                  <td>{ko.workshop.kinds[g.kind] ?? g.kind}</td>
                  <td>
                    <code>{g.key}</code>
                  </td>
                  <td>
                    {t.states[g.state]}
                    {g.differs.length > 0 && ` (${g.differs.join(", ")})`}
                  </td>
                  <td>
                    {g.members.map((m) => (
                      <Link key={m.element_id} to={`/workshop/${m.session_id}`} className="tag">
                        #{m.session_id}
                      </Link>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
