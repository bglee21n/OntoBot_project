// 36.15 합성 예시 데이터(TRD 17.17) — 타입마다 CSV 하나. 서버가 게시본 설계도로 검사하고(하나라도 틀리면
// 표 전체를 받지 않는다) 자산화 zip 의 seed/ 에 넣는다. 실제 고객 데이터가 아님은 올리는 사람이 밝힌다.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { api } from "../../api/client";
import type { EngagementDetail, SeedOut } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.adoption.seeds;

export function SeedsPanel({ e }: { e: EngagementDetail }) {
  const qc = useQueryClient();
  const key = ["adoption", "engagement", String(e.id), "seeds"];
  const list = useQuery({ queryKey: key, queryFn: () => api.get<SeedOut[]>(`/adoption/engagements/${e.id}/seeds`) });
  const [objectType, setObjectType] = useState("");
  const [csv, setCsv] = useState("");
  const [synthetic, setSynthetic] = useState(false);
  const upload = useMutation({
    mutationFn: () => api.put<SeedOut>(`/adoption/engagements/${e.id}/seeds/${encodeURIComponent(objectType.trim())}`, { csv, synthetic }),
    onSuccess: async () => {
      setCsv("");
      setSynthetic(false);
      await qc.invalidateQueries({ queryKey: key });
    },
  });
  const remove = useMutation({
    mutationFn: (type: string) => api.del(`/adoption/engagements/${e.id}/seeds/${encodeURIComponent(type)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  });
  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    upload.mutate();
  };

  return (
    <section className="stack">
      <h2>{t.title}</h2>
      <p className="muted">{t.intro}</p>
      {list.isError && <ErrorBox error={list.error} />}
      {list.data && list.data.length > 0 && (
        <table>
          <tbody>
            {list.data.map((s) => (
              <tr key={s.object_type}>
                <td>
                  <code>{s.object_type}</code>
                </td>
                <td>{t.rows(s.rows)}</td>
                <td>{s.pii_columns.length > 0 && <span className="warn-text">{t.pii(s.pii_columns.join(", "))}</span>}</td>
                <td className="actions">
                  {e.can_edit && (
                    <button type="button" className="link" onClick={() => remove.mutate(s.object_type)}>
                      {ko.common.delete}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {list.data?.length === 0 && <p className="muted">{t.none}</p>}
      {e.can_edit && (
        <form className="card stack" onSubmit={submit}>
          <div className="row">
            <input aria-label={t.objectType} placeholder={t.objectType} value={objectType} maxLength={64} onChange={(ev) => setObjectType(ev.target.value)} />
            <input
              type="file"
              accept=".csv,text/csv"
              aria-label={t.file}
              onChange={async (ev) => {
                const f = ev.target.files?.[0];
                if (f) setCsv(await f.text());
              }}
            />
          </div>
          <textarea className="plain" aria-label={t.csv} placeholder={t.csvHint} rows={4} value={csv} onChange={(ev) => setCsv(ev.target.value)} />
          <label>
            <input type="checkbox" checked={synthetic} onChange={(ev) => setSynthetic(ev.target.checked)} /> {t.synthetic}
          </label>
          <div>
            <button type="submit" disabled={upload.isPending || !objectType.trim() || !csv.trim() || !synthetic}>
              {t.upload}
            </button>
          </div>
          {upload.isError && <ErrorBox error={upload.error} />}
          {upload.data && upload.data.pii_columns.length > 0 && <p className="notice warn">{t.pii(upload.data.pii_columns.join(", "))}</p>}
        </form>
      )}
      {remove.isError && <ErrorBox error={remove.error} />}
    </section>
  );
}
