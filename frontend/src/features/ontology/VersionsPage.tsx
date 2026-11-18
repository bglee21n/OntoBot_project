import { Link, useNavigate } from "react-router-dom";

import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { useCreateDraft, useVersions } from "./queries";
import { Pager, usePaged } from "../../components/Pager";
import { formatDateTime } from "../../app/format";
import { EmptyState, PageHeader, RowMenu, useRowLink } from "../../components/page";

export function VersionsPage() {
  const versions = useVersions();
  const create = useCreateDraft();
  const navigate = useNavigate();

  const createDraft = (parentId: number | null) =>
    create.mutate(parentId, { onSuccess: (v) => navigate(`/ontology/versions/${v.id}`) });

  const { rows: pagedRows, pager } = usePaged(versions.data);
  const rowLink = useRowLink();
  return (
    <section className="stack">
      <PageHeader
        id="ontology"
        title={ko.nav.ontology}
        actions={
          <button type="button" onClick={() => createDraft(null)} disabled={create.isPending}>
            {ko.ontology.newDraft}
          </button>
        }
      />
      {create.isError && <ErrorBox error={create.error} />}
      {versions.isPending && <p>{ko.common.loading}</p>}
      {versions.isError && <ErrorBox error={versions.error} />}
      {versions.data?.length === 0 && <EmptyState message={ko.ontology.noVersions} />}
      {versions.data && versions.data.length > 0 && (
        <>
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
            {pagedRows.map((v) => (
              <tr key={v.id} className="row-link" onClick={rowLink(`/ontology/versions/${v.id}`)}>
                <td>{ko.ontology.version(v.version_no)}</td>
                <td>
                  <span className={`badge ${v.status}`}>{ko.status[v.status]}</span>
                </td>
                <td>{formatDateTime(v.created_at)}</td>
                <td>{v.published_at ? formatDateTime(v.published_at) : "—"}</td>
                <td className="actions">
                  <Link to={`/ontology/versions/${v.id}`}>{ko.ontology.open}</Link>
                  {v.status !== "draft" && <RowMenu items={[{ label: ko.ontology.copyToDraft, onSelect: () => createDraft(v.id) }]} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Pager pager={pager} />
        </>
      )}
    </section>
  );
}
