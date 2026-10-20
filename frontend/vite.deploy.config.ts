import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 배포 콘솔 화면(ADR-0014) — 앱과 따로 빌드해 배포 콘솔 이미지에 넣는다(앱 릴리스가 깨져도 화면이 떠야 한다).
// 개발: npm run dev:deploy → /api 를 배포 콘솔(:8100)로 넘긴다.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist-deploy",
    emptyOutDir: true,
    rollupOptions: { input: "deploy.html" },
  },
  server: {
    port: 5174,
    proxy: { "/api": "http://localhost:8100", "/healthz": "http://localhost:8100" },
  },
});
