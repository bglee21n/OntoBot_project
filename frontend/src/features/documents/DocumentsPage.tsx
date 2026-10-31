import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../../api/client";
import type { DocumentDetail, DocumentLinkOut, DocumentOut, GroupChoice, LinkScanOut } from "../../api/types";
import { useMe } from "../../app/auth";
import { ErrorBox } from "../../components/ErrorBox";
import { ko } from "../../i18n/ko";

const t = ko.documents;

/**
 * 문서(Phase 16a) — 데이터 그룹별 문서 목록·등록·청크 보기. 내가 속한 그룹의 문서만 보인다.
 * 임베딩은 워커가 하므로, 색인 중인 문서가 있으면 목록을 몇 초마다 다시 읽는다.
 */
export function DocumentsPage() {
  const params = useParams();
  const docId = params.docId ? Number(params.docId) : null;
  const me = useMe();
  const canUpload = me.data?.roles.some((r) => r === "modeler" || r === "admin") ?? false;
  const list = useQuery({
    queryKey: ["documents"],
    queryFn: () => api.get<DocumentOut[]>("/documents"),
    refetchInterval: (q) => (q.state.data?.some((d) => d.status === "pending") ? 3000 : false),
  });

  return (
    <section className="stack">
      <h1>{t.title}</h1>
      <p className="muted">{t.intro}</p>
      {canUpload && <UploadForm />}
      {list.isError && <ErrorBox error={list.error} />}
      {list.data?.length === 0 && <p className="notice">{t.none}</p>}
      {list.data && list.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>{t.docTitle}</th>
              <th>{t.group}</th>
              <th>{t.size}</th>
              <th>{t.status}</th>
              <th>{t.added}</th>
            </tr>
          </thead>
          <tbody>
            {list.data.map((d) => (
              <tr key={d.id} className={d.id === docId ? "selected-row" : ""}>
                <td>
                  <Link to={`/documents/${d.id}`}>{d.title}</Link>
                  <div className="muted">{d.filename}</div>
                </td>
                <td>{d.group_name}</td>
                <td>{t.sizeOf(d.chars, d.chunks)}</td>
                <td>
                  <StatusBadge d={d} />
                </td>
                <td>{new Date(d.created_at).toLocaleString("ko-KR")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {docId !== null && <DocumentView id={docId} canEdit={canUpload} />}
    </section>
  );
}

function StatusBadge({ d }: { d: DocumentOut }) {
  if (d.status === "indexed") return <span className="badge succeeded">{t.indexed}</span>;
  if (d.status === "failed") return <span className="badge failed">{t.failed}</span>;
  return (
    <span>
      <span className="badge running">{t.pending(d.embedded, d.chunks)}</span>
      {d.error && <div className="error-text">{d.error}</div>}
    </span>
  );
}

function UploadForm() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [group, setGroup] = useState("");
  const groups = useQuery({
    queryKey: ["documents", "groups"],
    queryFn: () => api.get<GroupChoice[]>("/documents/groups"),
  });
  const upload = useMutation({
    mutationFn: (form: FormData) => api.upload<DocumentOut>("/documents", form),
    onSuccess: (d) => {
      setTitle("");
      if (fileRef.current) fileRef.current.value = "";
      void qc.invalidateQueries({ queryKey: ["documents"] });
      navigate(`/documents/${d.id}`);
    },
  });
  const only = groups.data?.length === 1 ? groups.data[0] : undefined;
  const chosen = group || (only ? String(only.id) : "");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file || !chosen) return;
    const form = new FormData();
    form.append("file", file);
    form.append("group_id", chosen);
    if (title.trim()) form.append("title", title.trim());
    upload.mutate(form);
  };
  if (groups.data?.length === 0) return <p className="notice warn">{t.noGroup}</p>;
  return (
    <form className="card stack" onSubmit={submit}>
      <strong>{t.upload}</strong>
      <p className="muted">{t.uploadHint}</p>
      <div className="row">
        <input ref={fileRef} type="file" aria-label={t.file} accept=".txt,.md,.pdf" />
        <input aria-label={t.docTitle} placeholder={t.titleHint} value={title} maxLength={255} onChange={(e) => setTitle(e.target.value)} />
        <label className="row">
          {t.group}
          <select value={chosen} onChange={(e) => setGroup(e.target.value)}>
            <option value="" disabled>
              {t.chooseGroup}
            </option>
            {groups.data?.map((g) => (
              <option key={g.id} value={g.id}>
                {g.display_name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={upload.isPending || !chosen}>
          {upload.isPending ? t.uploading : t.add}
        </button>
      </div>
      {upload.isError && <ErrorBox error={upload.error} />}
    </form>
  );
}

function DocumentView({ id, canEdit }: { id: number; canEdit: boolean }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["documents", id],
    queryFn: () => api.get<DocumentDetail>(`/documents/${id}`),
    refetchInterval: (s) => (s.state.data?.status === "pending" ? 3000 : false),
  });
  const done = () => void qc.invalidateQueries({ queryKey: ["documents"] });
  const reindex = useMutation({ mutationFn: () => api.post<DocumentOut>(`/documents/${id}/reindex`), onSuccess: done });
  const remove = useMutation({
    mutationFn: () => api.del(`/documents/${id}`),
    onSuccess: () => {
      done();
      navigate("/documents");
    },
  });
  if (q.isError) return <ErrorBox error={q.error} />;
  if (!q.data) return <p>{ko.common.loading}</p>;
  const d = q.data;
  return (
    <div className="card stack">
      <div className="row">
        <h2>{d.title}</h2>
        <StatusBadge d={d} />
        <span className="spacer" />
        {canEdit && (
          <>
            <button type="button" className="link" disabled={reindex.isPending} onClick={() => reindex.mutate()}>
              {t.reindex}
            </button>
            <button
              type="button"
              className="link"
              disabled={remove.isPending}
              onClick={() => window.confirm(t.confirmDelete(d.title)) && remove.mutate()}
            >
              {t.remove}
            </button>
          </>
        )}
      </div>
      <div className="muted">
        {d.filename} · {d.media_type} · {d.group_name}
        {d.embedding_model && ` · ${t.model(d.embedding_model)}`}
      </div>
      {(reindex.isError || remove.isError) && <ErrorBox error={reindex.error ?? remove.error} />}
      <DocumentLinks id={id} canEdit={canEdit} />
      <h3>{t.chunks(d.chunk_list.length)}</h3>
      <p className="muted">{t.chunkHint}</p>
      <ol className="doc-chunks">
        {d.chunk_list.map((c) => (
          <li key={c.no} value={c.no}>
            <div className="muted">
              #{c.no} · {t.at(c.start_char)} {!c.embedded && <span className="badge running">{t.notEmbedded}</span>}
            </div>
            <pre>{c.text}</pre>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** 문서 ↔ 객체 링크(Phase 16c). 모델러·관리자는 후보 찾기·확정·거부, 그 밖에는 확정된 링크만 본다. */
function DocumentLinks({ id, canEdit }: { id: number; canEdit: boolean }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const links = useQuery({
    queryKey: ["documents", id, "links"],
    queryFn: () => api.get<DocumentLinkOut[]>(`/documents/${id}/links`),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["documents", id, "links"] });
  const scan = useMutation({
    mutationFn: () => api.post<LinkScanOut>(`/documents/${id}/links/scan`),
    onSuccess: refresh,
  });
  const decide = useMutation({
    mutationFn: ({ link, status }: { link: number; status: DocumentLinkOut["status"] }) =>
      api.put<null>(`/documents/${id}/links/${link}`, { status }),
    onSuccess: () => {
      refresh();
      void qc.invalidateQueries({ queryKey: ["object"] });
    },
  });
  // pk 는 URL 이 아니라 라우터 상태로 넘긴다(개인정보 규칙).
  const explore = (l: DocumentLinkOut) =>
    navigate("/explore", {
      state: { explore: { type: l.object_type, typeDisplayName: l.type_display_name, pk: l.object_pk, title: l.title } },
    });
  const r = scan.data;
  const choices: { status: DocumentLinkOut["status"]; label: string }[] = [
    { status: "confirmed", label: t.confirm },
    { status: "rejected", label: t.reject },
    { status: "candidate", label: t.undo },
  ];
  return (
    <div className="stack">
      <div className="row">
        <h3>{t.links}</h3>
        <span className="spacer" />
        {canEdit && (
          <button type="button" disabled={scan.isPending} onClick={() => scan.mutate()}>
            {scan.isPending ? t.scanning : t.scan}
          </button>
        )}
      </div>
      <p className="muted">{canEdit ? t.linksHint : t.linksReaderHint}</p>
      {r && (
        <p className={r.truncated ? "notice warn" : "notice"}>
          {t.scanResult(r.found, r.added, r.removed, r.scanned_objects)}
          {r.truncated && ` ${t.scanTruncated}`}
        </p>
      )}
      {(links.isError || scan.isError || decide.isError) && (
        <ErrorBox error={links.error ?? scan.error ?? decide.error} />
      )}
      {links.data?.length === 0 && <p className="muted">{t.noLinks}</p>}
      {links.data && links.data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>{t.object}</th>
              <th>{t.match}</th>
              <th>{t.linkState}</th>
              {canEdit && <th />}
            </tr>
          </thead>
          <tbody>
            {links.data.map((l) => (
              <tr key={l.id}>
                <td>
                  <button type="button" className="link" onClick={() => explore(l)}>
                    {l.title}
                  </button>
                  <div className="muted">{l.type_display_name}</div>
                </td>
                <td>
                  <code>{l.match_text}</code>
                  <div className="muted">{t.matchAt(t.matchKind[l.match_kind] ?? l.match_kind, l.occurrences, l.first_chunk_no)}</div>
                </td>
                <td>
                  <span className={`badge ${STATUS_CLASS[l.status]}`}>{t.linkStatus[l.status]}</span>
                </td>
                {canEdit && (
                  <td>
                    <div className="row">
                      {choices
                        .filter((c) => c.status !== l.status)
                        .map((c) => (
                          <button
                            key={c.status}
                            type="button"
                            className="link"
                            disabled={decide.isPending}
                            onClick={() => decide.mutate({ link: l.id, status: c.status })}
                          >
                            {c.label}
                          </button>
                        ))}
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

const STATUS_CLASS: Record<DocumentLinkOut["status"], string> = {
  candidate: "running",
  confirmed: "succeeded",
  rejected: "failed",
};
