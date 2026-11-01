import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../../api/client";
import type { SheetKindOut, SheetOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { engagementKey, useEngagement, useSheetKinds } from "./queries";
import { type Payload, SheetForm } from "./SheetForm";

const t = ko.adoption.sheet;

export function SheetPage() {
  const { engagementId = "", kind = "" } = useParams();
  const kinds = useSheetKinds();
  const detail = useEngagement(engagementId);
  const sheetKey = ["adoption", "engagement", engagementId, "sheet", kind];
  // 다시 불러오기마다 편집 상태를 새로 연다(저장으로는 다시 열지 않는다 — 저장 결과를 보여야 한다).
  const [generation, setGeneration] = useState(0);
  const sheet = useQuery({
    queryKey: sheetKey,
    queryFn: () => api.get<SheetOut>(`/adoption/engagements/${engagementId}/sheets/${kind}`),
  });

  if (kinds.isPending || detail.isPending || sheet.isPending) return <p>{ko.common.loading}</p>;
  if (kinds.isError) return <ErrorBox error={kinds.error} />;
  if (detail.isError) return <ErrorBox error={detail.error} />;
  if (sheet.isError) return <ErrorBox error={sheet.error} />;
  const declared = kinds.data.find((k) => k.kind === kind);
  if (!declared) return <p className="error">{t.unknownKind}</p>;

  return (
    <section className="stack">
      <p className="no-print">
        <Link to={`/adoption/${engagementId}`}>← {t.back}</Link> · {detail.data.name}
      </p>
      <Editor
        key={`${kind}-${generation}`}
        engagementId={engagementId}
        declared={declared}
        initial={sheet.data}
        canEdit={detail.data.can_edit}
        reload={async () => {
          await sheet.refetch();
          setGeneration((g) => g + 1);
        }}
      />
    </section>
  );
}

function Editor({
  engagementId,
  declared,
  initial,
  canEdit,
  reload,
}: {
  engagementId: string;
  declared: SheetKindOut;
  initial: SheetOut;
  canEdit: boolean;
  reload: () => void;
}) {
  const qc = useQueryClient();
  const [payload, setPayload] = useState<Payload>(initial.payload as Payload);
  const [dirty, setDirty] = useState(false);
  // 판 비교 저장 — 읽을 때(또는 마지막 저장에서) 받은 판을 보낸다.
  const [revision, setRevision] = useState(initial.revision);
  const save = useMutation({
    mutationFn: () =>
      api.put<SheetOut>(`/adoption/engagements/${engagementId}/sheets/${declared.kind}`, { revision, payload }),
    onSuccess: async (saved) => {
      setRevision(saved.revision);
      setPayload(saved.payload as Payload);
      setDirty(false);
      await qc.invalidateQueries({ queryKey: engagementKey(engagementId), exact: true });
    },
  });
  const conflict = save.error instanceof ApiError && save.error.problem.code === "revision_conflict";

  return (
    <div className="stack">
      <h1>
        {declared.number} {declared.title}
      </h1>
      <div className="row no-print">
        {canEdit ? (
          <button type="button" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            {t.save}
          </button>
        ) : (
          <span className="muted">{t.readOnly}</span>
        )}
        <button type="button" className="link" onClick={() => window.print()}>
          {t.print}
        </button>
        <span className="muted">
          {revision > 0 ? `${ko.adoption.revision} ${revision}` : ko.adoption.notSaved}
        </span>
        {dirty && <span className="warn-text">{t.unsaved}</span>}
        {!dirty && save.isSuccess && <span className="ok-text">{t.saved}</span>}
      </div>
      {conflict ? (
        <div className="notice warn no-print">
          {t.conflict}{" "}
          <button type="button" onClick={reload}>
            {t.reload}
          </button>
        </div>
      ) : (
        save.isError && <ErrorBox error={save.error} />
      )}
      <SheetForm
        kind={declared}
        payload={payload}
        readOnly={!canEdit}
        onChange={(next) => {
          setPayload(next);
          setDirty(true);
        }}
      />
    </div>
  );
}
