import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import type { ElementOut, ElementReview } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.workshop;

type Payload = Record<string, unknown>;
type Field = {
  key: string;
  type?: "select" | "check" | "list" | "area";
  options?: string[];
};

// 종류별 편집 필드. 모양의 정본은 backend workshop/elements.py 다(서버가 다시 검증한다).
const FIELDS: Record<string, Field[]> = {
  object_type: [
    { key: "api_name" },
    { key: "display_name" },
    { key: "description", type: "area" },
    { key: "pk_property" },
    { key: "title_property" },
  ],
  property: [
    { key: "object_type" },
    { key: "api_name" },
    { key: "display_name" },
    {
      key: "data_type",
      type: "select",
      options: ["string", "int", "decimal", "bool", "date", "datetime", "enum"],
    },
    { key: "enum_values", type: "list" },
    { key: "description", type: "area" },
  ],
  link_type: [
    { key: "api_name" },
    { key: "display_name" },
    { key: "from_type" },
    { key: "to_type" },
    { key: "cardinality", type: "select", options: ["one", "many"] },
    { key: "fk_property" },
  ],
  action_candidate: [{ key: "name" }, { key: "description", type: "area" }],
  golden_query: [
    { key: "question", type: "area" },
    { key: "expected_answer", type: "area" },
    { key: "answerable", type: "check" },
  ],
};

function str(v: unknown): string {
  if (Array.isArray(v)) {
    return v.join(", ");
  }
  return v === null || v === undefined ? "" : String(v);
}

/** 화면에 보일 이름 한 줄. */
function headline(el: ElementOut): string {
  const p = el.payload;
  switch (el.kind) {
    case "property":
      return `${str(p.object_type)}.${str(p.api_name)} — ${str(p.display_name)} (${str(p.data_type)})`;
    case "link_type":
      return `${str(p.from_type)} —${str(p.api_name)}→ ${str(p.to_type)} (${str(p.display_name)})`;
    case "action_candidate":
      return str(p.name);
    case "golden_query":
      return str(p.question);
    default:
      return `${str(p.api_name)} — ${str(p.display_name)}`;
  }
}

/** 편집 폼 값 → payload. 빈 칸은 null 로 보낸다(서버 기본값을 쓰게). */
function toPayload(kind: string, draft: Record<string, string | boolean>): Payload {
  const out: Payload = {};
  for (const f of FIELDS[kind] ?? []) {
    const v = draft[f.key];
    if (f.type === "check") {
      out[f.key] = Boolean(v);
    } else if (f.type === "list") {
      const items = String(v ?? "")
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean);
      out[f.key] = items.length > 0 ? items : null;
    } else {
      const text = String(v ?? "").trim();
      if (text || f.key === "description" || f.key === "expected_answer" || f.key === "fk_property") {
        out[f.key] = text || null;
      }
    }
  }
  return out;
}

export function ElementCard({
  element,
  editable,
  seqOf,
  onChanged,
}: {
  element: ElementOut;
  editable: boolean;
  seqOf: Map<number, number>;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string | boolean>>({});
  const save = useMutation({
    mutationFn: (body: ElementReview) => api.patch<ElementOut>(`/workshop/elements/${element.id}`, body),
    onSuccess: () => {
      setEditing(false);
      onChanged();
    },
  });

  const startEdit = () => {
    const init: Record<string, string | boolean> = {};
    for (const f of FIELDS[element.kind] ?? []) {
      const v = element.payload[f.key];
      init[f.key] = f.type === "check" ? v !== false : str(v);
    }
    setDraft(init);
    save.reset();
    setEditing(true);
  };

  return (
    <div className={`card element element-${element.status}`}>
      <div className="row">
        <strong>{headline(element)}</strong>
        <span className="spacer" />
        <span className={`badge el-${element.status}`}>{t.elementStatus[element.status]}</span>
      </div>
      {typeof element.payload.description === "string" && element.payload.description && (
        <p className="muted">{element.payload.description}</p>
      )}
      {element.kind === "golden_query" && (
        <p className="muted">
          {t.fields.expected_answer}: {str(element.payload.expected_answer) || "—"}
        </p>
      )}
      <ul className="evidence">
        {element.evidence.map((ev, i) => (
          <li key={i} className={ev.ok ? "" : "evidence-bad"}>
            <span className="muted">
              {t.evidence} #{seqOf.get(ev.utterance_id) ?? "?"}
            </span>{" "}
            “{ev.quote}”{!ev.ok && <div className="error-text">⚠ {t.evidenceMissing}</div>}
          </li>
        ))}
      </ul>

      {editing ? (
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate({
              status: "confirmed",
              payload: toPayload(element.kind, draft),
            });
          }}
        >
          {element.kind === "golden_query" && <p className="muted">{t.goldenHint}</p>}
          {(FIELDS[element.kind] ?? []).map((f) => {
            const label = t.fields[f.key] ?? f.key;
            const value = draft[f.key];
            const set = (v: string | boolean) => setDraft({ ...draft, [f.key]: v });
            if (f.type === "check") {
              return (
                <label key={f.key} className="check">
                  <input type="checkbox" checked={Boolean(value)} onChange={(e) => set(e.target.checked)} />
                  {label}
                </label>
              );
            }
            return (
              <label key={f.key}>
                {label}
                {f.type === "select" ? (
                  <select value={String(value)} onChange={(e) => set(e.target.value)}>
                    {f.options?.map((o) => (
                      <option key={o}>{o}</option>
                    ))}
                  </select>
                ) : f.type === "area" ? (
                  <textarea
                    className="plain"
                    rows={2}
                    value={String(value)}
                    onChange={(e) => set(e.target.value)}
                  />
                ) : (
                  <input value={String(value)} onChange={(e) => set(e.target.value)} />
                )}
              </label>
            );
          })}
          <div className="row">
            <button type="submit" disabled={save.isPending}>
              {t.save}
            </button>
            <button type="button" className="link" onClick={() => setEditing(false)}>
              {t.cancel}
            </button>
          </div>
        </form>
      ) : (
        editable && (
          <div className="row">
            <button
              type="button"
              disabled={save.isPending || !element.evidence_ok}
              title={element.evidence_ok ? undefined : t.evidenceMissing}
              onClick={() => save.mutate({ status: "confirmed" })}
            >
              {t.confirm}
            </button>
            <button type="button" className="link" onClick={startEdit}>
              {t.edit}
            </button>
            <button
              type="button"
              className="link danger"
              disabled={save.isPending}
              onClick={() => save.mutate({ status: "rejected" })}
            >
              {t.reject}
            </button>
          </div>
        )
      )}
      {save.isError && <ErrorBox error={save.error} />}
    </div>
  );
}
