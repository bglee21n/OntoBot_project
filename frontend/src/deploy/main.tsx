import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "../app/styles.css";
import { DeployApp } from "./DeployApp";

const root = document.getElementById("root");
if (!root) {
  throw new Error("#root 요소가 없습니다");
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: 1 } } })}>
      <DeployApp />
    </QueryClientProvider>
  </StrictMode>,
);
