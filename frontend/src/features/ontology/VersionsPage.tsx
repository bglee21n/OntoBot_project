import { Link, useNavigate } from "react-router-dom";

import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { useCreateDraft, useVersions } from "./queries";

export function VersionsPage() {
  const versions = useVersions();
  const create = useCreateDraft();
  const navigate = useNavigate();

  const createDraft = (parentId: number | null) =>
    create.mutate(parentId, { onSuccess: (v) => navigate(`/ontology/versions/${v.id}`) });

  return (
    <section className="stack">
      <div className="row">
        <h1>{ko.ontology.versions}</h1>
        <span className="spacer" />
        <button type="button" onClick={() => createDraft(null)} disabled={create.isPending}>
          {ko.ontology.newDraft}
        </button>
      </div>
      {create.isError && <ErrorBox error={create.error} />}
      {versions.isPending && <p>{ko.common.loading}</p>}
      {versions.isError && <ErrorBox error={versions.error} />}
      {versions.data?.length === 0 && <p className="muted">{ko.ontology.noVersions}</p>}
      {versions.data && versions.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>버전</th>
              <th>상태</th>
              <th>만든 시각</th>
              <th>게시 시각</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {versions.data.map((v) => (
              <tr key={v.id}>
                <td>{ko.ontology.version(v.version_no)}</td>
                <td>
                  <span className={`badge ${v.status}`}>{ko.status[v.status]}</span>
                </td>
                <td>{new Date(v.created_at).toLocaleString("ko-KR")}</td>
                <td>{v.published_at ? new Date(v.published_at).toLocaleString("ko-KR") : "—"}</td>
                <td className="actions">
                  <Link to={`/ontology/versions/${v.id}`}>{ko.ontology.open}</Link>
                  {v.status !== "draft" && (
                    <button type="button" className="link" onClick={() => createDraft(v.id)}>
                      {ko.ontology.copyToDraft}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
