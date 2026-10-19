import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { ChangeOut, VersionDetail, VersionSummary } from "../../api/types";

export const V = "/ontology/versions";
const listKey = ["ontology", "versions"];
const detailKey = (id: number) => ["ontology", "version", id];

export function useVersions() {
  return useQuery({ queryKey: listKey, queryFn: () => api.get<VersionSummary[]>(V) });
}

export function useVersion(id: number) {
  return useQuery({ queryKey: detailKey(id), queryFn: () => api.get<VersionDetail>(`${V}/${id}`) });
}

export function useDiff(id: number, base: number | null) {
  return useQuery({
    queryKey: ["ontology", "diff", id, base],
    queryFn: () => api.get<ChangeOut[]>(`${V}/${id}/diff?base=${base}`),
    enabled: base !== null,
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
