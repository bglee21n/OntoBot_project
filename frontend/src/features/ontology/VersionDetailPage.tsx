import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import { api } from "../../api/client";
import type { ChangeImpactOut, VersionDetail } from "../../api/types";
import { useMe } from "../../app/auth";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";
import { ActionTypesSection } from "./ActionTypes";
import { FunctionsSection } from "./Functions";
import { LinkTypeForm, ObjectTypeForm } from "./forms";
import { SchemaGraph } from "./graph/SchemaGraph";
import { ObjectTypeCard } from "./ObjectTypeCard";
import { useDiff, useImpact, useVersion, useVersionMutation, useVersions, V } from "./queries";

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
  const me = useMe();
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "graph" ? "graph" : "list";
  const isModeler = me.data?.roles.includes("modeler") ?? false;
  const othersDraft = editable && v.created_by != null && me.data != null && v.created_by !== me.data.id;
  const publish = useVersionMutation(v.id, () => api.post(`${V}/${v.id}/publish`));
  const impact = useImpact(v.id, editable);
  // 정의 문서 밖에서 깨지는 참조(행 정책이 지우는 속성을 쓴다)도 게시를 막는다 — 서버가 다시 확인한다.
  const blocked = impact.data?.some((i) => i.refs.some((r) => r.blocking && r.kind !== "document")) ?? false;

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
            disabled={publish.isPending || v.issues.length > 0 || blocked}
            title={v.issues.length > 0 || blocked ? ko.ontology.issuesTitle : undefined}
            onClick={() => window.confirm(ko.ontology.publishConfirm) && publish.mutate(undefined)}
          >
            {ko.ontology.publish}
          </button>
        )}
      </div>
      {publish.isError && <ErrorBox error={publish.error} />}
      {!editable && <p className="notice">{ko.ontology.readonly}</p>}
      {othersDraft && <p className="notice warn">{ko.ontology.draftOwner(v.created_by_name ?? `#${v.created_by}`)}</p>}

      {editable && <Issues issues={v.issues} />}
      {editable && impact.isError && <ErrorBox error={impact.error} />}
      {editable && impact.data && impact.data.length > 0 && <ImpactPanel impacts={impact.data} />}
      <div className="row tabs" role="tablist">
        {(["list", "graph"] as const).map((x) => (
          <button
            key={x}
            type="button"
            role="tab"
            aria-selected={view === x}
            className={view === x ? "tab active" : "tab"}
            onClick={() => setParams(x === "graph" ? { view: "graph" } : {}, { replace: true })}
          >
            {x === "graph" ? ko.ontology.viewGraph : ko.ontology.viewList}
          </button>
        ))}
      </div>
      {view === "graph" ? (
        <SchemaGraph v={v} editable={editable && isModeler} canSaveLayout={isModeler} />
      ) : (
        <ListView v={v} editable={editable} />
      )}
    </section>
  );
}

function ListView({ v, editable }: { v: VersionDetail; editable: boolean }) {
  const addType = useVersionMutation(v.id, (body: unknown) => api.post(`${V}/${v.id}/object-types`, body));
  const addLink = useVersionMutation(v.id, (body: unknown) => api.post(`${V}/${v.id}/link-types`, body));
  const delLink = useVersionMutation(v.id, (linkId: number) => api.del(`${V}/${v.id}/link-types/${linkId}`));
  return (
    <>
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

      <ActionTypesSection v={v} editable={editable} />
      <FunctionsSection v={v} editable={editable} />
    </>
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

const IMPACT_KIND = ko.ontology.publishImpact.kind;

/** 게시 영향(Phase 17a) — 바뀌는 요소마다 참조처. 막는 것은 빨간 표시, 나머지는 알리기만 한다. */
function ImpactPanel({ impacts }: { impacts: ChangeImpactOut[] }) {
  const t = ko.ontology.publishImpact;
  return (
    <div className="card stack">
      <strong>{t.title}</strong>
      <p className="muted">{t.hint}</p>
      <ul>
        {impacts.map((i) => (
          <li key={`${i.element}:${i.path}`}>
            <code>{i.path}</code> {t.change[i.kind]} · {t.element[i.element]}
            {i.refs.length === 0 ? (
              <span className="muted"> — {t.noRefs}</span>
            ) : (
              <ul>
                {i.refs.map((r) => (
                  <li key={`${r.kind}:${r.label}`} className={r.blocking ? "error-text" : undefined}>
                    <span className="badge">{IMPACT_KIND[r.kind] ?? r.kind}</span> {r.label}
                    {r.blocking && ` — ${t.blocking}`}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </div>
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
