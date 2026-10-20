import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { ActionPreviewData, ActionRunOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { RequestPanel, RunCard } from "../actions/ActionsPage";

const t = ko.chat.action;

/**
 * 채팅의 Action 요청(TRD 17.1-5). 실행하지 않는다 — 미리보기까지다.
 * 대조를 통과했으면 서버가 미리보기(실행 기록)를 만들어 두었으므로 그 기록을 지금 상태로 다시 읽어 보여 주고,
 * 통과하지 못했으면 확인 사항과 함께 서버가 확인한 값만 채운 요청 폼을 보여 준다(사용자가 고른다).
 */
export function ActionProposal({ data }: { data: ActionPreviewData }) {
  return (
    <div className="answer">
      <div className="row">
        <span className="badge">{t.badge}</span>
        <strong>{data.display_name}</strong>
        {data.target_pk && (
          <span>
            {data.target_title ?? data.target_pk} · <code>{data.target_pk}</code>
          </span>
        )}
        <span className="spacer" />
        <Link to="/actions">{t.openActions}</Link>
      </div>
      <div className="answer-text">{data.text}</div>
      {data.run_id != null ? <PreviewRun runId={data.run_id} /> : <Incomplete data={data} />}
    </div>
  );
}

function PreviewRun({ runId }: { runId: number }) {
  // 승인·거둠·롤백으로 상태가 바뀐다 — 대화 기록에 저장한 값이 아니라 지금 기록을 읽는다.
  const run = useQuery({
    queryKey: ["action-runs", "one", runId],
    queryFn: () => api.get<ActionRunOut>(`/action-runs/${runId}`),
  });
  if (run.isError) {
    return <ErrorBox error={run.error} />;
  }
  return run.data ? <RunCard run={run.data} open /> : <p className="muted">{ko.common.loading}</p>;
}

function Incomplete({ data }: { data: ActionPreviewData }) {
  return (
    <>
      {data.issues.length > 0 && (
        <div className="notice">
          <strong>{t.issues}</strong>
          <ul>
            {data.issues.map((issue, i) => (
              <li key={i}>{issue}</li>
            ))}
          </ul>
        </div>
      )}
      {data.candidates.length > 0 && (
        <div className="muted">
          {t.candidates}:{" "}
          {data.candidates.map((c) => `${c.title} (${c.pk})`).join(", ")}
        </div>
      )}
      <p className="muted">{t.fill}</p>
      <RequestPanel
        title={data.display_name}
        initial={{
          action: data.action_type,
          pk: data.target_pk ?? "",
          params: Object.fromEntries(Object.entries(data.params).map(([k, v]) => [k, String(v)])),
          candidates: data.candidates,
        }}
      />
    </>
  );
}
