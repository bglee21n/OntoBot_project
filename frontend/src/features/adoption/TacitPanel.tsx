// 인터뷰 화면 옆 패널(TRD 17.17 결정 A11) — 이은 도입의 36.9 암묵지 기록을 읽기 전용으로.
// 도입 구성원이 아니면 서버가 404 를 주므로 패널을 그리지 않는다. 주제·프롬프트·추출은 바꾸지 않는다.
import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { SheetOut } from "../../api/types";
import { ko } from "../../i18n/ko";
import type { Row } from "./SheetForm";
import { useSheetKinds } from "./queries";

const t = ko.adoption.tacitPanel;

export function TacitPanel({ engagementId }: { engagementId: number }) {
  const kinds = useSheetKinds();
  const sheet = useQuery({
    queryKey: ["adoption", "engagement", String(engagementId), "sheet", "tacit"],
    queryFn: () => api.get<SheetOut>(`/adoption/engagements/${engagementId}/sheets/tacit`),
    retry: false,
  });
  const declared = kinds.data?.find((k) => k.kind === "tacit");
  if (!sheet.data || !declared) return null;
  const rows: { label: string; text: string }[] = [];
  for (const sec of declared.sections) {
    const value = sheet.data.payload[sec.key];
    if (!Array.isArray(value)) continue;
    (value as Row[]).forEach((row, i) => {
      const lead = sec.rows[i]?.slice(0, sec.columns.findIndex((c) => !c.fixed)).filter(Boolean).join(" · ");
      for (const c of sec.columns) {
        const v = !c.fixed ? row[c.key] : undefined;
        if (typeof v === "string" && v.trim()) rows.push({ label: `${lead || sec.title || ""} — ${c.label}`, text: v });
      }
    });
  }
  return (
    <details className="card">
      <summary>{t.title}</summary>
      {rows.length === 0 ? (
        <p className="muted">{t.empty}</p>
      ) : (
        <dl>
          {rows.map((r, i) => (
            <div key={i}>
              <dt className="muted">{r.label}</dt>
              <dd>{r.text}</dd>
            </div>
          ))}
        </dl>
      )}
    </details>
  );
}
