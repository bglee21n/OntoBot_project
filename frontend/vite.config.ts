import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 개발 서버는 /api 를 백엔드로 넘긴다 — 운영에서는 백엔드가 빌드 산출물을 직접 서빙하므로
// 같은 오리진이 되어 세션 쿠키(SameSite=Lax)가 그대로 붙는다(TRD 9.1).
// CDN을 쓰지 않는다: 모든 자산은 빌드에 포함된다(TRD 15절 폐쇄망).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/healthz": "http://localhost:8000",
    },
  },
});
