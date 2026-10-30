import { type FormEvent, useState } from "react";

import type { DataType, LinkTypeDef, ObjectTypeCreate, ObjectTypePatch, PropertyDef } from "../../api/types";
import { ko } from "../../i18n/ko";

const DATA_TYPES: DataType[] = ["string", "int", "decimal", "bool", "date", "datetime", "enum"];
const PII_KINDS = ["", "name", "phone", "email", "rrn", "address", "account", "other"] as const;
type PiiKind = NonNullable<PropertyDef["pii_kind"]>;

/** 속성 추가·수정. 수정할 때 api_name 은 잠근다 — 동기화 매핑이 그 이름을 쓴다. */
export function PropertyForm({
  initial,
  onSubmit,
  onCancel,
  pending,
}: {
  initial?: PropertyDef;
  onSubmit: (p: PropertyDef) => void;
  onCancel?: () => void;
  pending: boolean;
}) {
  const [p, setP] = useState<PropertyDef>(
    initial ?? { api_name: "", display_name: "", data_type: "string", nullable: true, indexed: false },
  );
  const [enumText, setEnumText] = useState((initial?.enum_values ?? []).join(", "));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const enumValues = enumText
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    onSubmit({ ...p, enum_values: p.data_type === "enum" ? enumValues : null });
  };

  return (
    <form onSubmit={submit} className="grid-form">
      <label>
        {ko.ontology.apiName}
        <input
          value={p.api_name}
          onChange={(e) => setP({ ...p, api_name: e.target.value })}
          placeholder="risk_grade"
          disabled={initial !== undefined}
          required
        />
      </label>
      <label>
        {ko.ontology.displayName}
        <input value={p.display_name} onChange={(e) => setP({ ...p, display_name: e.target.value })} required />
      </label>
      <label>
        {ko.ontology.dataType}
        <select value={p.data_type} onChange={(e) => setP({ ...p, data_type: e.target.value as DataType })}>
          {DATA_TYPES.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </label>
      {p.data_type === "enum" && (
        <label>
          {ko.ontology.enumValues}
          <input value={enumText} onChange={(e) => setEnumText(e.target.value)} required />
        </label>
      )}
      <label>
        {ko.ontology.piiKind}
        <select
          value={p.pii_kind ?? ""}
          onChange={(e) => setP({ ...p, pii_kind: e.target.value ? (e.target.value as PiiKind) : null })}
        >
          {PII_KINDS.map((k) => (
            <option key={k} value={k}>
              {k || "—"}
            </option>
          ))}
        </select>
      </label>
      <label className="check">
        <input type="checkbox" checked={p.nullable ?? true} onChange={(e) => setP({ ...p, nullable: e.target.checked })} />
        {ko.ontology.nullable}
      </label>
      <label className="check">
        <input type="checkbox" checked={p.indexed ?? false} onChange={(e) => setP({ ...p, indexed: e.target.checked })} />
        {ko.ontology.indexed}
      </label>
      <div className="row">
        <button type="submit" disabled={pending}>
          {initial ? ko.common.save : ko.common.add}
        </button>
        {onCancel && (
          <button type="button" className="link" onClick={onCancel}>
            {ko.common.cancel}
          </button>
        )}
      </div>
    </form>
  );
}

/** 객체 타입 추가. pk·제목 속성은 이름만 정하고, 실제 속성은 이어서 추가한다(게시 전 검사가 짚는다). */
export function ObjectTypeForm({ onSubmit, pending }: { onSubmit: (t: ObjectTypeCreate) => void; pending: boolean }) {
  const [t, setT] = useState<ObjectTypeCreate>({
    api_name: "",
    display_name: "",
    pk_property: "",
    title_property: "",
    properties: [],
    deprecated: false,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit(t);
  };
  return (
    <form onSubmit={submit} className="grid-form">
      <label>
        {ko.ontology.apiName}
        <input value={t.api_name} onChange={(e) => setT({ ...t, api_name: e.target.value })} placeholder="Facility" required />
      </label>
      <label>
        {ko.ontology.displayName}
        <input value={t.display_name} onChange={(e) => setT({ ...t, display_name: e.target.value })} required />
      </label>
      <label>
        {ko.ontology.pk}
        <input value={t.pk_property} onChange={(e) => setT({ ...t, pk_property: e.target.value })} placeholder="facility_id" required />
      </label>
      <label>
        {ko.ontology.title}
        <input value={t.title_property} onChange={(e) => setT({ ...t, title_property: e.target.value })} placeholder="name" required />
      </label>
      <div className="row">
        <button type="submit" disabled={pending}>
          {ko.ontology.addObjectType}
        </button>
      </div>
    </form>
  );
}

/**
 * 링크 타입 추가·수정. 그래프에서 끌어 만들면 양 끝이 미리 채워진다(``initial``).
 * 수정할 때(``lockName``) api_name 은 잠근다. FK 속성 후보(``fkChoices``)를 주면 출발 타입의 속성에서 고른다.
 */
export function LinkTypeForm({
  typeNames,
  onSubmit,
  pending,
  initial,
  lockName = false,
  fkChoices,
}: {
  typeNames: string[];
  onSubmit: (l: LinkTypeDef) => void;
  pending: boolean;
  initial?: Partial<LinkTypeDef>;
  lockName?: boolean;
  fkChoices?: (fromType: string) => string[];
}) {
  const first = typeNames[0] ?? "";
  const [l, setL] = useState<LinkTypeDef>({
    api_name: "",
    display_name: "",
    from_type: first,
    to_type: first,
    cardinality: "one",
    source: { kind: "fk_property", property: "" },
    deprecated: false,
    ...initial,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit(l);
  };
  const typeSelect = (value: string, set: (v: string) => void) => (
    <select value={value} onChange={(e) => set(e.target.value)} required>
      {typeNames.map((n) => (
        <option key={n}>{n}</option>
      ))}
    </select>
  );
  const choices = fkChoices?.(l.from_type);
  const setFk = (property: string) => setL({ ...l, source: { kind: "fk_property", property } });
  return (
    <form onSubmit={submit} className="grid-form">
      <label>
        {ko.ontology.apiName}
        <input
          value={l.api_name}
          onChange={(e) => setL({ ...l, api_name: e.target.value })}
          placeholder="owned_by"
          disabled={lockName}
          required
        />
      </label>
      <label>
        {ko.ontology.displayName}
        <input value={l.display_name} onChange={(e) => setL({ ...l, display_name: e.target.value })} required />
      </label>
      <label>
        {ko.ontology.fromType}
        {typeSelect(l.from_type, (v) => setL({ ...l, from_type: v }))}
      </label>
      <label>
        {ko.ontology.toType}
        {typeSelect(l.to_type, (v) => setL({ ...l, to_type: v }))}
      </label>
      <label>
        {ko.ontology.cardinality}
        <select value={l.cardinality} onChange={(e) => setL({ ...l, cardinality: e.target.value as "one" | "many" })}>
          <option>one</option>
          <option>many</option>
        </select>
      </label>
      {l.source.kind === "fk_property" ? (
        <label>
          {ko.ontology.fkProperty}
          {choices ? (
            <select value={l.source.property ?? ""} onChange={(e) => setFk(e.target.value)} required>
              <option value="">—</option>
              {choices.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          ) : (
            <input
              value={l.source.property ?? ""}
              onChange={(e) => setFk(e.target.value)}
              placeholder="owner_org_id"
              required
            />
          )}
        </label>
      ) : (
        <p className="muted">{ko.ontology.joinMapping}</p>
      )}
      {lockName && (
        <label className="check">
          <input
            type="checkbox"
            checked={l.deprecated}
            onChange={(e) => setL({ ...l, deprecated: e.target.checked })}
          />
          {ko.ontology.deprecated}
        </label>
      )}
      <div className="row">
        <button type="submit" disabled={pending || typeNames.length === 0}>
          {lockName ? ko.common.save : ko.ontology.addLinkType}
        </button>
      </div>
    </form>
  );
}

/** 객체 타입 머리(표시 이름·설명·pk·제목·폐기) 고치기. api_name 은 바꾸지 않는다. */
export function ObjectTypeHeadForm({
  initial,
  propertyNames,
  onSubmit,
  pending,
}: {
  initial: ObjectTypePatch;
  propertyNames: string[];
  onSubmit: (t: ObjectTypePatch) => void;
  pending: boolean;
}) {
  const [t, setT] = useState<ObjectTypePatch>(initial);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit(t);
  };
  const propSelect = (value: string | null | undefined, set: (v: string) => void) => (
    <select value={value ?? ""} onChange={(e) => set(e.target.value)} required>
      {/* 아직 없는 이름이면 그대로 보여 준다 — 게시 전 검사가 짚는다. */}
      {value && !propertyNames.includes(value) && <option>{value}</option>}
      {propertyNames.map((n) => (
        <option key={n}>{n}</option>
      ))}
    </select>
  );
  return (
    <form onSubmit={submit} className="grid-form">
      <label>
        {ko.ontology.displayName}
        <input value={t.display_name ?? ""} onChange={(e) => setT({ ...t, display_name: e.target.value })} required />
      </label>
      <label>
        {ko.ontology.description}
        <input value={t.description ?? ""} onChange={(e) => setT({ ...t, description: e.target.value || null })} />
      </label>
      <label>
        {ko.ontology.pk}
        {propSelect(t.pk_property, (v) => setT({ ...t, pk_property: v }))}
      </label>
      <label>
        {ko.ontology.title}
        {propSelect(t.title_property, (v) => setT({ ...t, title_property: v }))}
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={t.deprecated ?? false}
          onChange={(e) => setT({ ...t, deprecated: e.target.checked })}
        />
        {ko.ontology.deprecated}
      </label>
      <div className="row">
        <button type="submit" disabled={pending}>
          {ko.common.save}
        </button>
      </div>
    </form>
  );
}
