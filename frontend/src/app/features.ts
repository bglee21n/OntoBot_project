import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { FeaturesOut } from "../api/types";

/** 화면 기능 플래그(Phase 18, 결정 13) — 판정은 서버(`ontobot.features`). 못 받으면 모두 꺼진 것으로 본다. */
export function useFeatures(): FeaturesOut {
  const q = useQuery({
    queryKey: ["config", "features"],
    queryFn: () => api.get<FeaturesOut>("/config/features"),
    staleTime: Infinity,
  });
  return q.data ?? { closed_network: true, voice_input: false };
}
