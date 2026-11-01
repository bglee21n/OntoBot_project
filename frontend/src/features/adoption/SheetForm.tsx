// 범용 양식 편집기(TRD 17.17, 결정 A2) — 서버의 양식 선언(adoption.sheets)을 그대로 그린다.
// 양식마다 화면을 따로 만들지 않는다. 칸 이름·순서의 정본은 서버 선언이고, 검증도 서버가 같은 선언으로 한다.
import type { ColumnOut, SectionOut, SheetKindOut } from "../../api/types";
import { ko } from "../../i18n/ko";

const t = ko.adoption.sheet;

export type Cell = string | boolean | null | undefined;
export type Row = Record<string, Cell>;
export type Payload = Record<string, Row | Row[] | undefined>;

interface Props {
  kind: SheetKindOut;
  payload: Payload;
  readOnly: boolean;
  onChange: (next: Payload) => void;
}

export function SheetForm({ kind, payload, readOnly, onChange }: Props) {
  const set = (key: string, value: Row | Row[]) => onChange({ ...payload, [key]: value });
  return (
    <div className="stack sheet">
      <p className="muted">{kind.intro}</p>
      {kind.sections.map((sec) => (
        <section key={sec.key} className="stack">
          {sec.title && <h3>{sec.title}</h3>}
          {sec.layout === "fields" ? (
            <FieldsView sec={sec} value={(payload[sec.key] as Row | undefined) ?? {}} readOnly={readOnly} onChange={(v) => set(sec.key, v)} />
          ) : (
            <TableView sec={sec} rows={(payload[sec.key] as Row[] | undefined) ?? []} readOnly={readOnly} onChange={(v) => set(sec.key, v)} />
          )}
          {sec.note && <p className="notice">{sec.note}</p>}
        </section>
      ))}
    </div>
  );
}

function FieldsView({ sec, value, readOnly, onChange }: { sec: SectionOut; value: Row; readOnly: boolean; onChange: (v: Row) => void }) {
  return (
    <div className="sheet-fields">
      {sec.columns.map((c) => (
        <label key={c.key} className={c.kind === "long" ? "wide" : ""}>
          <span>{c.label}</span>
          <CellInput col={c} value={value[c.key]} readOnly={readOnly} onChange={(v) => onChange({ ...value, [c.key]: v })} />
        </label>
      ))}
    </div>
  );
}

function TableView({ sec, rows, readOnly, onChange }: { sec: SectionOut; rows: Row[]; readOnly: boolean; onChange: (v: Row[]) => void }) {
  const fixedRows = sec.rows.length > 0;
  const hasInputs = sec.columns.some((c) => !c.fixed);
  const setCell = (i: number, key: string, v: Cell) => {
    const next = [...rows];
    while (next.length <= i) next.push({});
    next[i] = { ...next[i], [key]: v };
    onChange(next);
  };
  const shown = fixedRows ? sec.rows.length : rows.length;
  // 인쇄할 때 행을 더하는 표는 매뉴얼의 빈 행 수만큼 빈 줄을 채운다.
  const blanks = fixedRows ? 0 : Math.max(sec.blank_rows - rows.length, 0);
  return (
    <div className="stack">
      <table className="sheet-table">
        <thead>
          <tr>
            {sec.columns.map((c, ci) => (
              <th key={ci}>{c.label}</th>
            ))}
            {!fixedRows && !readOnly && <th className="no-print" />}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: shown }, (_, i) => (
            <tr key={i}>
              {sec.columns.map((c, ci) =>
                c.fixed ? (
                  <td key={ci} className="fixed">
                    {sec.rows[i]?.[ci]}
                  </td>
                ) : (
                  <td key={ci}>
                    <CellInput
                      col={c}
                      value={rows[i]?.[c.key]}
                      placeholder={sec.rows[i]?.[ci] || undefined}
                      readOnly={readOnly}
                      onChange={(v) => setCell(i, c.key, v)}
                    />
                  </td>
                ),
              )}
              {!fixedRows && !readOnly && (
                <td className="no-print">
                  <button type="button" className="link" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
                    {t.removeRow}
                  </button>
                </td>
              )}
            </tr>
          ))}
          {Array.from({ length: blanks }, (_, i) => (
            <tr key={`blank-${i}`} className="print-only">
              {sec.columns.map((_, ci) => (
                <td key={ci}>&nbsp;</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!fixedRows && hasInputs && !readOnly && (
        <div className="no-print">
          <button type="button" disabled={rows.length >= sec.max_rows} onClick={() => onChange([...rows, {}])}>
            {t.addRow}
          </button>
        </div>
      )}
    </div>
  );
}

function CellInput({
  col,
  value,
  placeholder,
  readOnly,
  onChange,
}: {
  col: ColumnOut;
  value: Cell;
  placeholder?: string;
  readOnly: boolean;
  onChange: (v: Cell) => void;
}) {
  const text = typeof value === "string" ? value : "";
  const common = {
    "aria-label": col.label,
    title: col.pii ? t.pii : undefined,
    className: col.pii ? "pii" : undefined,
    disabled: readOnly,
  };
  switch (col.kind) {
    case "check":
      return <input type="checkbox" {...common} checked={value === true} onChange={(e) => onChange(e.target.checked)} />;
    case "choice":
      return (
        <select {...common} value={text} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">—</option>
          {col.options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      );
    case "long":
      return (
        <textarea {...common} className={`plain ${common.className ?? ""}`} rows={2} maxLength={4000} placeholder={placeholder} value={text} onChange={(e) => onChange(e.target.value)} />
      );
    default:
      return <input {...common} maxLength={500} placeholder={placeholder} value={text} onChange={(e) => onChange(e.target.value)} />;
  }
}
