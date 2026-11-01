// 양식 → 제품 보내기(TRD 17.17 결정 A5) — 미리보기 → 적용. 적용은 미리보기의 지문을 보내고,
// 그 사이 바뀐 것이 있으면 서버가 409(preview_stale)로 멈춘다.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../../api/client";
import type { EngagementDetail, SendAppliedOut, SendPreviewOut, VersionSummary } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { V } from "../ontology/queries";
import { engagementKey } from "./queries";

const t = ko.adoption.send;
const BRIDGES = ["draft", "golden", "targets", "accounts"] as const;
type Bridge = (typeof BRIDGES)[number];

export function SendPanel({ e }: { e: EngagementDetail }) {
  const allowed = BRIDGES.filter((b) => e.can_send[b]);
  const [bridge, setBridge] = useState<Bridge | null>(null);
  if (!e.can_edit) return null;
  return (
    <section className="stack">
      <h2>{t.title}</h2>
      <p className="muted">{t.intro}</p>
      <nav className="row tabs" role="tablist">
        {BRIDGES.map((b) => (
          <button
            key={b}
            type="button"
            role="tab"
            aria-selected={bridge === b}
            className={`tab${bridge === b ? " active" : ""}`}
            disabled={!allowed.includes(b)}
            title={allowed.includes(b) ? undefined : t.noRole}
            onClick={() => setBridge(b)}
          >
            {t.bridges[b]}
          </button>
        ))}
      </nav>
      {bridge && <Bridgework key={bridge} e={e} bridge={bridge} />}
    </section>
  );
}

function Bridgework({ e, bridge }: { e: EngagementDetail; bridge: Bridge }) {
  const qc = useQueryClient();
  // draft 를 고르는 보내기에서만(설계도 버전 목록은 modeler·admin 만 읽는다)
  const versions = useQuery({
    queryKey: ["ontology", "versions"],
    queryFn: () => api.get<VersionSummary[]>(V),
    enabled: bridge === "draft",
  });
  const [versionId, setVersionId] = useState<number | null>(null);
  const [result, setResult] = useState<SendAppliedOut | null>(null);
  const base = `/adoption/engagements/${e.id}/send/${bridge}`;
  const preview = useMutation({
    mutationFn: () => api.post<SendPreviewOut>(`${base}/preview`, { version_id: versionId }),
    onSuccess: () => setResult(null),
  });
  const apply = useMutation({
    mutationFn: (fingerprint: string) => api.post<SendAppliedOut>(`${base}/apply`, { version_id: versionId, fingerprint }),
    onSuccess: async (r) => {
      setResult(r);
      if (r.version_id) setVersionId(r.version_id);
      preview.reset();
      await qc.invalidateQueries({ queryKey: engagementKey(e.id), exact: true });
      await qc.invalidateQueries({ queryKey: ["ontology"] });
    },
  });
  const stale = apply.error instanceof ApiError && apply.error.problem.code === "preview_stale";
  const drafts = (versions.data ?? []).filter((v) => v.status === "draft");
  const p = preview.data;

  return (
    <div className="card stack">
      {bridge === "draft" && (
        <label className="row">
          {t.draftTarget}
          <select aria-label={t.draftTarget} value={versionId ?? ""} onChange={(ev) => setVersionId(ev.target.value ? Number(ev.target.value) : null)}>
            <option value="">{t.newDraft}</option>
            {drafts.map((v) => (
              <option key={v.id} value={v.id}>
                v{v.version_no} (draft #{v.id})
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="row">
        <button type="button" disabled={preview.isPending} onClick={() => preview.mutate()}>
          {t.preview}
        </button>
        {p && (
          <button type="button" disabled={p.applicable === 0 || apply.isPending} onClick={() => apply.mutate(p.fingerprint)}>
            {t.apply(p.applicable)}
          </button>
        )}
        {p && p.applicable === 0 && <span className="muted">{t.nothing}</span>}
      </div>
      {preview.isError && <ErrorBox error={preview.error} />}
      {stale ? <p className="notice warn">{t.stale}</p> : apply.isError && <ErrorBox error={apply.error} />}
      {p && p.changes.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>{t.cols.source}</th>
              <th>{t.cols.target}</th>
              <th>{t.cols.field}</th>
              <th>{t.cols.before}</th>
              <th>{t.cols.after}</th>
              <th>{t.cols.status}</th>
            </tr>
          </thead>
          <tbody>
            {p.changes.map((c, i) => (
              <tr key={i}>
                <td className="muted">{c.source}</td>
                <td>{c.target}</td>
                <td>{c.field ?? "—"}</td>
                <td className="muted">{c.before ?? "—"}</td>
                <td>{c.after ?? "—"}</td>
                <td>
                  {c.blocked === null ? (
                    <span className="badge succeeded">{t.willApply}</span>
                  ) : c.blocked === "" ? (
                    <span className="badge archived">{t.same}</span>
                  ) : (
                    <span className="warn-text">{c.blocked}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {result && (
        <div className="notice stack">
          <span className="ok-text">{t.applied(result.applied)}</span>
          {result.version_id && bridge === "draft" && <Link to={`/ontology/versions/${result.version_id}`}>{t.openDraft}</Link>}
          {result.golden_set_id && <Link to={`/eval/${result.golden_set_id}`}>{t.openGolden}</Link>}
          {Object.keys(result.passwords).length > 0 && (
            <div className="notice warn">
              <p>{t.passwords}</p>
              <ul>
                {Object.entries(result.passwords).map(([u, pw]) => (
                  <li key={u}>
                    <code>{u}</code> · <code>{pw}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
