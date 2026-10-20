import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api } from "../../api/client";
import type { GoldenSetDetail, ItemIn, ItemOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.eval;
const STATUSES = ["ok", "empty", "failed", "not_query"] as const;
const FAULTS = ["none", "executor_error", "graph_down", "timeout"] as const;

interface Draft {
  key: string;
  question: string;
  expected_status: (typeof STATUSES)[number];
  expected_failure: string;
  required_values: string;
  answerable: boolean;
  test_role: string;
  fault: (typeof FAULTS)[number];
}

const EMPTY: Draft = {
  key: "",
  question: "",
  expected_status: "ok",
  expected_failure: "",
  required_values: "",
  answerable: true,
  test_role: "",
  fault: "none",
};

function fromItem(i: ItemOut): Draft {
  return {
    key: i.key ?? "",
    question: i.question,
    expected_status: i.expected_status,
    expected_failure: i.expected_failure ?? "",
    required_values: (i.required_values ?? []).join(", "),
    answerable: i.answerable ?? true,
    test_role: i.test_role ?? "",
    fault: i.fault ?? "none",
  };
}

function toBody(d: Draft): ItemIn {
  return {
    key: d.key.trim() || null,
    question: d.question.trim(),
    expected_status: d.expected_status,
    expected_failure: d.expected_failure.trim() || null,
    required_values: d.required_values
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean),
    answerable: d.answerable,
    test_role: d.test_role.trim() || null,
    fault: d.fault,
  };
}

/** 골든셋 문항 — 보기와 고치기. 정본이 도메인 팩 파일인 문항(manual)도 화면에서 고칠 수 있지만,
 * 팩을 다시 적재하면 파일 내용으로 돌아간다. */
export function ItemsTable({ set }: { set: GoldenSetDetail }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const save = useMutation({
    mutationFn: (d: Draft) =>
      editing === "new"
        ? api.post<ItemOut>(`/golden-sets/${set.id}/items`, toBody(d))
        : api.patch<ItemOut>(`/golden-sets/${set.id}/items/${editing}`, toBody(d)),
    onSuccess: () => {
      setEditing(null);
      void qc.invalidateQueries({ queryKey: ["golden-sets"] });
    },
  });
  const hasWorkshop = set.item_list.some((i) => i.source === "workshop");

  const open = (target: number | "new", d: Draft) => {
    save.reset();
    setDraft(d);
    setEditing(target);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate(draft);
  };

  const form = (
    <form className="grid-form card" onSubmit={submit}>
      <label>
        {t.key}
        <input value={draft.key} onChange={(e) => setDraft({ ...draft, key: e.target.value })} />
      </label>
      <label className="span-all">
        {t.question}
        <input value={draft.question} onChange={(e) => setDraft({ ...draft, question: e.target.value })} />
      </label>
      <label>
        {t.expected}
        <select
          value={draft.expected_status}
          onChange={(e) => setDraft({ ...draft, expected_status: e.target.value as Draft["expected_status"] })}
        >
          {STATUSES.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </label>
      <label>
        failure
        <input
          value={draft.expected_failure}
          onChange={(e) => setDraft({ ...draft, expected_failure: e.target.value })}
        />
      </label>
      <label className="span-all">
        {t.values} <span className="muted">({t.valuesHint})</span>
        <input
          value={draft.required_values}
          onChange={(e) => setDraft({ ...draft, required_values: e.target.value })}
        />
      </label>
      <label>
        {t.role}
        <input value={draft.test_role} onChange={(e) => setDraft({ ...draft, test_role: e.target.value })} />
      </label>
      <label>
        {t.fault}
        <select
          value={draft.fault}
          onChange={(e) => setDraft({ ...draft, fault: e.target.value as Draft["fault"] })}
        >
          {FAULTS.map((f) => (
            <option key={f}>{f}</option>
          ))}
        </select>
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={draft.answerable}
          onChange={(e) => setDraft({ ...draft, answerable: e.target.checked })}
        />
        {t.answerable}
      </label>
      <div className="row span-all">
        <button type="submit" disabled={!draft.question.trim() || save.isPending}>
          {t.save}
        </button>
        <button type="button" className="link" onClick={() => setEditing(null)}>
          {t.cancel}
        </button>
      </div>
      {save.isError && (
        <div className="span-all">
          <ErrorBox error={save.error} />
        </div>
      )}
    </form>
  );

  return (
    <div className="stack">
      {hasWorkshop && <p className="notice">{t.workshopHint}</p>}
      <table>
        <thead>
          <tr>
            <th>{t.key}</th>
            <th>{t.question}</th>
            <th>{t.expected}</th>
            <th>{t.values}</th>
            <th>{t.role}</th>
            <th>{t.fault}</th>
            <th>{t.source}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {set.item_list.map((i) => (
            <tr key={i.id}>
              <td>{i.key ?? "—"}</td>
              <td>
                {i.question}
                {i.expected_answer && <div className="muted">→ {i.expected_answer}</div>}
              </td>
              <td>
                {i.expected_status}
                {i.expected_failure ? `/${i.expected_failure}` : ""}
                {i.answerable === false && <span className="muted"> · {t.unanswerable}</span>}
              </td>
              <td>{(i.required_values ?? []).join(", ") || "—"}</td>
              <td>{i.test_role ?? "—"}</td>
              <td>{i.fault === "none" ? "—" : i.fault}</td>
              <td className="muted">{i.source}</td>
              <td className="actions">
                <button type="button" className="link" onClick={() => open(i.id, fromItem(i))}>
                  {t.edit}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing !== null ? (
        form
      ) : (
        <div>
          <button type="button" onClick={() => open("new", EMPTY)}>
            {t.addItem}
          </button>
        </div>
      )}
    </div>
  );
}
