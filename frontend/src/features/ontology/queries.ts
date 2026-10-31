import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type {
  ChangeImpactOut,
  ChangeOut,
  LayoutOut,
  ReferencesOut,
  VersionDetail,
  VersionSummary,
} from "../../api/types";

export const V = "/ontology/versions";
const listKey = ["ontology", "versions"];
const detailKey = (id: number) => ["ontology", "version", id];

export function useVersions() {
  return useQuery({ queryKey: listKey, queryFn: () => api.get<VersionSummary[]>(V) });
}

export function useVersion(id: number, enabled = true) {
  return useQuery({ queryKey: detailKey(id), queryFn: () => api.get<VersionDetail>(`${V}/${id}`), enabled });
}

export function useDiff(id: number, base: number | null) {
  return useQuery({
    queryKey: ["ontology", "diff", id, base],
    queryFn: () => api.get<ChangeOut[]>(`${V}/${id}/diff?base=${base}`),
    enabled: base !== null,
  });
}

/** 게시본 → 이 버전의 지우기·바꾸기마다 참조처(Phase 17a). 게시본이 없으면 빈 목록. */
export function useImpact(id: number, enabled: boolean) {
  return useQuery({
    queryKey: ["ontology", "impact", id],
    queryFn: () => api.get<ChangeImpactOut[]>(`${V}/${id}/impact`),
    enabled,
  });
}

/** 버전을 바꾸는 요청. 성공하면 그 버전과 목록을 다시 읽는다(서버가 정본이다). */
export function useVersionMutation<TArgs>(id: number, fn: (args: TArgs) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: detailKey(id) }),
        qc.invalidateQueries({ queryKey: listKey }),
        qc.invalidateQueries({ queryKey: ["ontology", "diff", id] }),
        qc.invalidateQueries({ queryKey: ["ontology", "impact", id] }),
      ]);
    },
  });
}

export function useCreateDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (parentId: number | null) => api.post<VersionDetail>(V, { parent_id: parentId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: listKey }),
  });
}

/** 스키마 그래프의 노드 위치 — 정의가 아니라 화면 상태다(게시본도 고칠 수 있다). */
export function useLayout(id: number) {
  return useQuery({ queryKey: ["ontology", "layout", id], queryFn: () => api.get<LayoutOut>(`${V}/${id}/layout`) });
}

export function useSaveLayout(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (nodes: LayoutOut["nodes"]) => api.put<LayoutOut>(`${V}/${id}/layout`, { nodes }),
    onSuccess: (data) => qc.setQueryData(["ontology", "layout", id], data),
  });
}

/** 지우거나 폐기하려는 요소를 무엇이 쓰는가 — 이름과 개수만. */
export function useReferences(
  id: number,
  target: { kind: "object_type" | "property" | "link_type"; name: string; type?: string } | null,
) {
  const params = target
    ? new URLSearchParams({ kind: target.kind, name: target.name, ...(target.type ? { type: target.type } : {}) })
    : null;
  return useQuery({
    queryKey: ["ontology", "references", id, params?.toString()],
    queryFn: () => api.get<ReferencesOut>(`${V}/${id}/references?${params}`),
    enabled: target !== null,
  });
}
