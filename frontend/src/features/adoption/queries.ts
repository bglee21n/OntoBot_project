import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import type { EngagementDetail, SheetKindOut } from "../../api/types";

/** 양식 선언은 서버 판이 바뀌지 않는 한 그대로다. */
export function useSheetKinds() {
  return useQuery({
    queryKey: ["adoption", "sheet-kinds"],
    queryFn: () => api.get<SheetKindOut[]>("/adoption/sheet-kinds"),
    staleTime: Infinity,
  });
}

export const engagementKey = (id: string | number) => ["adoption", "engagement", String(id)];

export function useEngagement(id: string) {
  return useQuery({
    queryKey: engagementKey(id),
    queryFn: () => api.get<EngagementDetail>(`/adoption/engagements/${id}`),
  });
}
