import { useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../../api/client";
import type { ObjectTypeOut, PropertyDef, PropertyOut, VersionDetail } from "../../api/types";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { LinkTypeForm, ObjectTypeForm, PropertyForm } from "./forms";
import { useDiff, useVersion, useVersionMutation, useVersions, V } from "./queries";

export function VersionDetailPage() {
  const id = Number(useParams().id);
  const version = useVersion(id);
  if (version.isPending) {
    return <p>{ko.common.loading}</p>;
  }
  if (version.isError) {
    return <ErrorBox error={version.error} />;
  }
  return <VersionEditor v={version.data} />;
}

function VersionEditor({ v }: { v: VersionDetail }) {
  const editable = v.status === "draft";
  const publish = useVersionMutation(v.id, () => api.post(`${V}/${v.id}/publish`));
  const addType = useVersionMutation(v.id, (body: unknown) => api.post(`${V}/${v.id}/object-types`, body));
  const addLink = useVersionMutation(v.id, (body: unknown) => api.post(`${V}/${v.id}/link-types`, body));
  const delLink = useVersionMutation(v.id, (linkId: number) => api.del(`${V}/${v.id}/link-types/${linkId}`));

  return (
    <section className="stack">
      <div className="row">
        <h1>{ko.ontology.version(v.version_no)}</h1>
        <span className={`badge ${v.status}`}>{ko.status[v.status]}</span>
        <span className="spacer" />
        <ExportLinks id={v.id} />
        {editable && (
          <button
            type="button"
            disabled={publish.isPending || v.issues.length > 0}
            title={v.issues.length > 0 ? ko.ontology.issuesTitle : undefined}
            onClick={() => window.confirm(ko.ontology.publishConfirm) && publish.mutate(undefined)}
          >
            {ko.ontology.publish}
          </button>
        )}
      </div>
      {publish.isError && <ErrorBox error={publish.error} />}
      {!editable && <p className="notice">{ko.ontology.readonly}</p>}

      {editable && <Issues issues={v.issues} />}
      {editable && <ImportPanel id={v.id} />}
      <DiffPanel v={v} />

      <h2>{ko.ontology.objectTypes}</h2>
      {v.object_types.map((t) => (
        <ObjectTypeCard key={t.id} versionId={v.id} t={t} editable={editable} />
      ))}
      {editable && (
        <details className="card">
          <summary>{ko.ontology.addObjectType}</summary>
          <ObjectTypeForm pending={addType.isPending} onSubmit={(t) => addType.mutate(t)} />
          {addType.isError && <ErrorBox error={addType.error} />}
        </details>
      )}

      <h2>{ko.ontology.linkTypes}</h2>
      <table>
        <thead>
          <tr>
            <th>{ko.ontology.apiName}</th>
            <th>{ko.ontology.displayName}</th>
            <th>{ko.ontology.fromType}</th>
            <th>{ko.ontology.toType}</th>
            <th>{ko.ontology.cardinality}</th>
            <th>{ko.ontology.fkProperty}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {v.link_types.map((l) => (
            <tr key={l.id}>
              <td>
                <code>{l.api_name}</code>
              </td>
              <td>{l.display_name}</td>
              <td>{l.from_type}</td>
              <td>{l.to_type}</td>
              <td>{l.cardinality}</td>
              <td>{l.source.property ?? l.source.kind}</td>
              <td className="actions">
                {editable && (
                  <button
                    type="button"
                    className="link danger"
                    onClick={() => window.confirm(ko.common.confirmDelete) && delLink.mutate(l.id)}
                  >
                    {ko.common.delete}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {delLink.isError && <ErrorBox error={delLink.error} />}
      {editable && (
        <details className="card">
          <summary>{ko.ontology.addLinkType}</summary>
          <LinkTypeForm
            typeNames={v.object_types.map((t) => t.api_name)}
            pending={addLink.isPending}
            onSubmit={(l) => addLink.mutate(l)}
          />
          {addLink.isError && <ErrorBox error={addLink.error} />}
        </details>
      )}
    </section>
  );
}

function Issues({ issues }: { issues: VersionDetail["issues"] }) {
  return (
    <div className={issues.length ? "card warn" : "card ok"}>
      <strong>{ko.ontology.issuesTitle}</strong>
      {issues.length === 0 ? (
        <p>{ko.ontology.noIssues}</p>
      ) : (
        <ul>
          {issues.map((i) => (
            <li key={`${i.path}:${i.message}`}>
              <code>{i.path}</code> {i.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ObjectTypeCard({ versionId, t, editable }: { versionId: number; t: ObjectTypeOut; editable: boolean }) {
  const base = `${V}/${versionId}/object-types/${t.id}`;
  const [editing, setEditing] = useState<number | null>(null);
  const addProp = useVersionMutation(versionId, (p: PropertyDef) => api.post(`${base}/properties`, p));
  const editProp = useVersionMutation(versionId, (a: { id: number; p: PropertyDef }) =>
    api.patch(`${base}/properties/${a.id}`, a.p),
  );
  const delProp = useVersionMutation(versionId, (propId: number) => api.del(`${base}/properties/${propId}`));
  const delType = useVersionMutation(versionId, () => api.del(base));
  const error = addProp.error ?? editProp.error ?? delProp.error ?? delType.error;

  const toDef = (p: PropertyOut): PropertyDef => {
    const def: PropertyDef & { id?: number } = { ...p };
    delete def.id;
    return def;
  };

  return (
    <article className="card stack">
      <div className="row">
        <h3>
          {t.display_name} <code>{t.api_name}</code>
        </h3>
        <span className="muted">
          {ko.ontology.pk} <code>{t.pk_property}</code> · {ko.ontology.title} <code>{t.title_property}</code>
        </span>
        <span className="spacer" />
        {editable && (
          <button
            type="button"
            className="link danger"
            onClick={() => window.confirm(ko.common.confirmDelete) && delType.mutate(undefined)}
          >
            {ko.common.delete}
          </button>
        )}
      </div>
      {t.description && <p className="muted">{t.description}</p>}
      <table>
        <thead>
          <tr>
            <th>{ko.ontology.apiName}</th>
            <th>{ko.ontology.displayName}</th>
            <th>{ko.ontology.dataType}</th>
            <th>{ko.ontology.nullable}</th>
            <th>{ko.ontology.indexed}</th>
            <th>{ko.ontology.piiKind}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {t.properties.map((p) =>
            editing === p.id ? (
              <tr key={p.id}>
                <td colSpan={7}>
                  <PropertyForm
                    initial={toDef(p)}
                    pending={editProp.isPending}
                    onCancel={() => setEditing(null)}
                    onSubmit={(def) => editProp.mutate({ id: p.id, p: def }, { onSuccess: () => setEditing(null) })}
                  />
                </td>
              </tr>
            ) : (
              <tr key={p.id}>
                <td>
                  <code>{p.api_name}</code>
                </td>
                <td>{p.display_name}</td>
                <td>
                  {p.data_type}
                  {p.enum_values ? ` (${p.enum_values.join(", ")})` : ""}
                </td>
                <td>{p.nullable ? "예" : "아니오"}</td>
                <td>{p.indexed ? "예" : ""}</td>
                <td>{p.pii_kind ?? ""}</td>
                <td className="actions">
                  {editable && (
                    <>
                      <button type="button" className="link" onClick={() => setEditing(p.id)}>
                        수정
                      </button>
                      <button
                        type="button"
                        className="link danger"
                        onClick={() => window.confirm(ko.common.confirmDelete) && delProp.mutate(p.id)}
                      >
                        {ko.common.delete}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
      {error && <ErrorBox error={error} />}
      {editable && (
        <details>
          <summary>{ko.ontology.addProperty}</summary>
          <PropertyForm key={t.properties.length} pending={addProp.isPending} onSubmit={(p) => addProp.mutate(p)} />
        </details>
      )}
    </article>
  );
}

function ImportPanel({ id }: { id: number }) {
  const [content, setContent] = useState("");
  const [format, setFormat] = useState<"yaml" | "json">("yaml");
  const run = useVersionMutation(id, () => api.post(`${V}/${id}/import`, { format, content }));

  const readFile = async (file: File | undefined) => {
    if (!file) return;
    setContent(await file.text());
    setFormat(file.name.endsWith(".json") ? "json" : "yaml");
  };

  return (
    <details className="card">
      <summary>{ko.ontology.import}</summary>
      <p className="muted">{ko.ontology.importHint}</p>
      <div className="row">
        <input type="file" accept=".yaml,.yml,.json" onChange={(e) => void readFile(e.target.files?.[0])} />
        <select value={format} onChange={(e) => setFormat(e.target.value as "yaml" | "json")}>
          <option value="yaml">YAML</option>
          <option value="json">JSON</option>
        </select>
      </div>
      <textarea rows={10} value={content} onChange={(e) => setContent(e.target.value)} spellCheck={false} />
      <button type="button" disabled={!content || run.isPending} onClick={() => run.mutate(undefined)}>
        {ko.ontology.importRun}
      </button>
      {run.isError && <ErrorBox error={run.error} />}
    </details>
  );
}

function ExportLinks({ id }: { id: number }) {
  // 같은 오리진 링크라 세션 쿠키가 붙는다. 파일명은 서버의 Content-Disposition 이 정한다.
  return (
    <span className="muted">
      {ko.ontology.export}: <a href={`/api/v1${V}/${id}/export?format=yaml`}>YAML</a> ·{" "}
      <a href={`/api/v1${V}/${id}/export?format=json`}>JSON</a>
    </span>
  );
}

function DiffPanel({ v }: { v: VersionDetail }) {
  const versions = useVersions();
  const [base, setBase] = useState<number | null>(v.parent_id ?? null);
  const diff = useDiff(v.id, base);
  const others = (versions.data ?? []).filter((x) => x.id !== v.id);

  return (
    <details className="card">
      <summary>{ko.ontology.diff}</summary>
      <label className="row">
        {ko.ontology.diffBase}
        <select value={base ?? ""} onChange={(e) => setBase(e.target.value ? Number(e.target.value) : null)}>
          <option value="">—</option>
          {others.map((x) => (
            <option key={x.id} value={x.id}>
              {ko.ontology.version(x.version_no)} ({ko.status[x.status]})
            </option>
          ))}
        </select>
      </label>
      {diff.isError && <ErrorBox error={diff.error} />}
      {diff.data && diff.data.length === 0 && <p>{ko.ontology.diffNone}</p>}
      {diff.data && diff.data.length > 0 && (
        <ul>
          {diff.data.map((c) => (
            <li key={`${c.kind}:${c.path}`}>
              <span className={`badge ${c.kind}`}>{ko.ontology.changeKind[c.kind]}</span> {c.element}{" "}
              <code>{c.path}</code>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}
