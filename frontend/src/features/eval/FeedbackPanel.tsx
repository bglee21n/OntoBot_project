import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import type { FeedbackItemOut, GoldenSetDetail, ItemOut, PromoteIn } from "../../api/types";
import { useMe } from "../../app/auth";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.eval;
const f = ko.eval.feedback;
const STATUSES = ["ok", "empty", "failed", "not_query", "action_preview"] as const;

interface Draft {
  question: string;
  expected_status: (typeof STATUSES)[number];
  required_values: string;
  absent_values: string;
  note: string;
}

/**
 * 싫어요 받은 답 → 이 골든셋의 문항 후보(Phase 15). 기대값은 사람이 적는다 — 피드백은 답이
 * 틀렸다는 신호일 뿐 맞는 답을 모른다. 질문만 미리 채운다.
 */
export function FeedbackPanel({ set }: { set: GoldenSetDetail }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  // 트레이스는 관리자만 본다(서버도 막는다).
  const isAdmin = useMe().data?.roles.includes("admin") ?? false;
  const list = useQuery({
    queryKey: ["feedback"],
    queryFn: () => api.get<FeedbackItemOut[]>("/feedback"),
  });
  const done = () => {
    setOpen(null);
    setDraft(null);
    void qc.invalidateQueries({ queryKey: ["feedback"] });
    void qc.invalidateQueries({ queryKey: ["golden-sets"] });
  };
  const promote = useMutation({
    mutationFn: ({ id, d }: { id: number; d: Draft }) =>
      api.post<ItemOut>(`/feedback/${id}/promote`, toBody(set.id, d)),
    onSuccess: done,
  });
  const dismiss = useMutation({
    mutationFn: (id: number) => api.post<void>(`/feedback/${id}/dismiss`, {}),
    onSuccess: done,
  });

  const start = (fb: FeedbackItemOut) => {
    setOpen(fb.id);
    setDraft({
      question: fb.question ?? "",
      expected_status: "ok",
      required_values: "",
      absent_values: "",
      note: fb.comment ? `${f.userSaid}: ${fb.comment}` : "",
    });
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (open !== null && draft && draft.question.trim()) {
      promote.mutate({ id: open, d: draft });
    }
  };

  return (
    <div className="stack">
      <h3>
        {f.title} <span className="muted">({list.data?.length ?? 0})</span>
      </h3>
      <p className="muted">{f.hint}</p>
      {list.isError && <ErrorBox error={list.error} />}
      {list.data?.length === 0 && <p className="muted">{f.none}</p>}
      {list.data && list.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>{t.question}</th>
              <th>{f.answer}</th>
              <th>{f.comment}</th>
              <th>{ko.admin.ts}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {list.data.map((fb) => (
              <tr key={fb.id} className={fb.id === open ? "selected-row" : ""}>
                <td>{fb.question ?? "—"}</td>
                <td>
                  {fb.answer_status && <span className="badge">{fb.answer_status}</span>} {fb.answer}
                  {fb.conditions && <div className="muted">{fb.conditions}</div>}
                  {isAdmin && fb.trace_id != null && <Link to={`/traces/${fb.trace_id}`}>{f.trace}</Link>}
                </td>
                <td>{fb.comment ?? "—"}</td>
                <td>{new Date(fb.created_at).toLocaleString("ko-KR")}</td>
                <td className="actions">
                  <button type="button" onClick={() => start(fb)}>
                    {f.promote}
                  </button>
                  <button
                    type="button"
                    className="link"
                    disabled={dismiss.isPending}
                    onClick={() => dismiss.mutate(fb.id)}
                  >
                    {f.dismiss}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {dismiss.isError && <ErrorBox error={dismiss.error} />}
      {open !== null && draft && (
        <form className="stack card" onSubmit={submit}>
          <h4>{f.formTitle(set.name)}</h4>
          <label>
            {t.question}
            <input
              value={draft.question}
              maxLength={1000}
              onChange={(e) => setDraft({ ...draft, question: e.target.value })}
            />
          </label>
          <label>
            {t.expected}
            <select
              value={draft.expected_status}
              onChange={(e) =>
                setDraft({ ...draft, expected_status: e.target.value as Draft["expected_status"] })
              }
            >
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t.values} <span className="muted">({t.valuesHint})</span>
            <input
              value={draft.required_values}
              onChange={(e) => setDraft({ ...draft, required_values: e.target.value })}
            />
          </label>
          <label>
            {f.absent} <span className="muted">({t.valuesHint})</span>
            <input
              value={draft.absent_values}
              onChange={(e) => setDraft({ ...draft, absent_values: e.target.value })}
            />
          </label>
          <label>
            {f.note}
            <input value={draft.note} onChange={(e) => setDraft({ ...draft, note: e.target.value })} />
          </label>
          <div className="row">
            <button type="submit" disabled={promote.isPending || !draft.question.trim()}>
              {f.save}
            </button>
            <button type="button" className="link" onClick={() => setOpen(null)}>
              {t.cancel}
            </button>
          </div>
          {promote.isError && <ErrorBox error={promote.error} />}
        </form>
      )}
    </div>
  );
}

function split(v: string): string[] {
  return v
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

function toBody(setId: number, d: Draft): PromoteIn {
  return {
    golden_set_id: setId,
    question: d.question.trim(),
    expected_status: d.expected_status,
    answerable: true,
    fault: "none",
    required_values: split(d.required_values),
    absent_values: split(d.absent_values),
    note: d.note.trim() || null,
  };
}
