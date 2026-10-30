import { useQuery } from "@tanstack/react-query";
import { Fragment, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import type { CitationData, FieldOut, MessageOut, ObjectCard } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.chat;
// 서버 grounding/grader.py 의 ESTIMATE_MARK 와 같은 표기.
const ESTIMATE_MARK = "(추정) ";

export type Status = NonNullable<MessageOut["status"]>;

export interface AnswerProps {
  status: Status;
  grade: number | null;
  text: string;
  conditions: string | null;
  graphAsOf: string | null;
  citations: CitationData[];
  failureReason: string | null;
  selected: Selection | null;
  onSelect: (sel: Selection) => void;
  // 저장된 답에만 — 스트리밍 중인 답은 아직 메시지 id 가 없다.
  feedback?: ReactNode;
}

/** 근거 카드로 열 대상 — 객체(또는 집계 행) 하나와 이 답이 그 객체에서 인용한 속성들. */
export interface Selection {
  target: string;
  title: string;
  fields: string[];
  /** 집계·Function 결과 행(q:N)의 값 — 답을 만들 때 대조한 그대로(다시 읽을 곳이 없다). */
  values?: CitationData["values"];
}

/** 인용을 객체 단위로 묶는다. 속성마다 칩을 두면 목록 답에서 칩이 수십 개가 된다. */
function groupCitations(citations: CitationData[]): Selection[] {
  const groups = new Map<string, Selection>();
  for (const c of citations) {
    const hash = c.ref.lastIndexOf("#");
    const target = c.ref.slice(0, hash);
    const g = groups.get(target) ?? { target, title: c.title, fields: [], values: c.values };
    g.fields.push(c.ref.slice(hash + 1));
    groups.set(target, g);
  }
  return [...groups.values()];
}

/** 조회 조건 — 서버가 계획에서 결정론적으로 만든 문장. 사용자가 해석이 맞는지 바로 본다. */
export function QueryConditions({ text, graphAsOf }: { text: string; graphAsOf: string | null }) {
  return (
    <div className="conditions">
      {t.conditions}: {text}
      {graphAsOf && <div>{t.graphAsOf(new Date(graphAsOf).toLocaleTimeString())}</div>}
    </div>
  );
}

export function GradeBadge({ grade }: { grade: number }) {
  return (
    <span className={`badge grade-${grade}`} title={t.gradeHint[grade]}>
      {["①", "②", "③"][grade - 1]} {t.grade[grade]}
    </span>
  );
}

/** 실패는 0건과 다른 모양으로 그린다 — "자료 없음"처럼 보이면 사용자가 사실로 믿는다(TRD 7.3). */
export function FailureNotice({ reason, message }: { reason: string | null; message: string }) {
  return (
    <div className="failure" role="alert">
      <strong>
        {t.failed}
        {reason ? ` · ${t.failureReason[reason] ?? reason}` : ""}
      </strong>
      <div>{message}</div>
    </div>
  );
}

/** 본문의 "(추정) " 표기를 눈에 띄는 표시로 바꾼다. 문장 자체는 서버가 대조한 그대로다. */
function withEstimates(text: string): ReactNode[] {
  const parts = text.split(ESTIMATE_MARK);
  return parts.flatMap((part, i) =>
    i === 0
      ? [part]
      : [
          <span key={`e${i}`} className="estimate">
            {t.estimate}
          </span>,
          " ",
          part,
        ],
  );
}

export function AnswerView(p: AnswerProps) {
  if (p.status === "failed") {
    return (
      <div className="answer">
        {p.conditions && <QueryConditions text={p.conditions} graphAsOf={p.graphAsOf} />}
        <FailureNotice reason={p.failureReason} message={p.text} />
        {p.feedback}
      </div>
    );
  }
  if (p.status === "not_query") {
    return (
      <div className="answer">
        <div className="answer-text muted">{p.text}</div>
      </div>
    );
  }
  return (
    <div className="answer">
      {p.conditions && <QueryConditions text={p.conditions} graphAsOf={p.graphAsOf} />}
      {p.status === "empty" ? (
        <div className="empty-result">
          <strong>{t.emptyResult}</strong> — {p.text}
        </div>
      ) : (
        <div className="answer-text">{withEstimates(p.text)}</div>
      )}
      <div className="row">
        {p.grade !== null && <GradeBadge grade={p.grade} />}
        {p.citations.length > 0 && (
          <div className="citations" aria-label={t.citations}>
            {groupCitations(p.citations).map((g) => (
              <button
                type="button"
                key={g.target}
                className={p.selected?.target === g.target ? "selected" : ""}
                onClick={() => p.onSelect(g)}
              >
                {g.title}
              </button>
            ))}
          </div>
        )}
      </div>
      {p.feedback}
    </div>
  );
}

/**
 * 근거 카드 — 인용한 객체의 지금 보이는 값과 값 출처(열 때마다 다시 읽는다).
 * 편집된 값은 "편집됨 · 원천 값 ○○"을 함께 보여 준다 — 편집 값이 낡았는지 사람이 판단할 수 있어야 한다.
 */
export function ObjectCardPanel({
  selection,
  onClose,
  explore = true,
}: {
  selection: Selection;
  onClose: () => void;
  // 탐색기로 가는 단추(탐색기 안에서는 끈다).
  explore?: boolean;
}) {
  const navigate = useNavigate();
  // 대상: o:Type/pk (pk 에 / 가 있을 수 있어 첫 / 만 가른다) 또는 q:n(집계 행)
  const { target, fields } = selection;
  const isObject = target.startsWith("o:");
  const slash = target.indexOf("/");
  const type = target.slice(2, slash);
  const pk = target.slice(slash + 1);
  const card = useQuery({
    queryKey: ["object", type, pk],
    queryFn: () =>
      api.get<ObjectCard>(`/objects/${encodeURIComponent(type)}/${encodeURIComponent(pk)}`),
    enabled: isObject,
  });
  return (
    <aside className="card object-card">
      <div className="row">
        <strong>{t.card.title}</strong>
        <span className="spacer" />
        <button type="button" className="link" onClick={onClose}>
          {t.card.close}
        </button>
      </div>
      {!isObject && (
        <>
          <h3>{selection.title}</h3>
          {selection.values?.length ? (
            <dl>
              {selection.values.map((v) => (
                <Fragment key={v.name}>
                  <dt>{v.label}</dt>
                  <dd>{fields.includes(v.name) ? <strong>{v.display}</strong> : v.display}</dd>
                </Fragment>
              ))}
            </dl>
          ) : null}
          <p className="muted">{selection.values?.length ? t.card.aggregateValues : t.card.aggregate}</p>
        </>
      )}
      {card.isPending && isObject && <p>{ko.common.loading}</p>}
      {card.isError && <ErrorBox error={card.error} />}
      {card.data && (
        <>
          <h3>{card.data.title}</h3>
          <div className="muted">
            {card.data.type_display_name} · <code>{card.data.pk}</code>
          </div>
          {explore && (
            <button
              type="button"
              className="link"
              onClick={() =>
                card.data &&
                navigate("/explore", {
                  // pk 는 URL 이 아니라 라우터 상태로 넘긴다(개인정보 규칙).
                  state: {
                    explore: {
                      type: card.data.type,
                      typeDisplayName: card.data.type_display_name,
                      pk: card.data.pk,
                      title: card.data.title,
                    },
                  },
                })
              }
            >
              {t.card.explore}
            </button>
          )}
          <dl>
            {card.data.fields.map((f) => (
              <FieldRow key={f.name} field={f} cited={fields.includes(f.name)} />
            ))}
          </dl>
          <div className="muted">
            {t.card.syncedAt}:{" "}
            {card.data.synced_at ? new Date(card.data.synced_at).toLocaleString() : t.card.neverSynced}
          </div>
          <div className="muted">
            {t.card.lineage}:{" "}
            {card.data.source
              ? [
                  t.card.lineageSource(card.data.source.data_source, card.data.source.origin ?? "—"),
                  card.data.source.sync_run_id != null && card.data.source.run_finished_at
                    ? t.card.lineageRun(
                        card.data.source.sync_run_id,
                        new Date(card.data.source.run_finished_at).toLocaleString(),
                      )
                    : null,
                ]
                  .filter(Boolean)
                  .join(" · ")
              : t.card.lineageNone}
          </div>
        </>
      )}
    </aside>
  );
}

function FieldRow({ field, cited }: { field: FieldOut; cited: boolean }) {
  const e = field.edit;
  const when = (v: string) => new Date(v).toLocaleString();
  return (
    <>
      <dt>{field.display_name}</dt>
      <dd>
        {cited ? <strong>{field.value}</strong> : field.value}
        {e && (
          <div className="edited">
            <span className="badge">{t.card.edited}</span> {t.card.sourceValue(e.source_value)}
            <span className="muted"> · {t.card.editedBy(e.edited_by ?? t.card.unknownUser, when(e.edited_at))}</span>
            {e.conflict && (
              <div className={e.conflict.status === "open" ? "error-text" : "muted"}>
                {t.card.conflict(
                  e.conflict.new_source_value,
                  e.conflict.source_synced_at ? when(e.conflict.source_synced_at) : "—",
                )}
                {e.conflict.status === "acknowledged" && ` · ${t.card.conflictAcknowledged}`}
              </div>
            )}
          </div>
        )}
      </dd>
    </>
  );
}
