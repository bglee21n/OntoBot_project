import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 개발 콘솔 화면(ADR-0015) — 개발 PC 전용. 개발 콘솔(:8200)이 dist-devconsole 을 직접 서빙한다.
// 화면 개발: npm run dev:devconsole → /api 를 :8200 으로 넘긴다.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist-devconsole",
    emptyOutDir: true,
    rollupOptions: { input: "devconsole.html" },
  },
  server: {
    port: 5175,
    proxy: { "/api": "http://127.0.0.1:8200", "/healthz": "http://127.0.0.1:8200" },
  },
});
