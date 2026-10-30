import { Navigate, Route, Routes } from "react-router-dom";

import { ActionsPage } from "../features/actions/ActionsPage";
import { AdminPage } from "../features/admin/AdminPage";
import { ChatPage } from "../features/chat/ChatPage";
import { EvalPage } from "../features/eval/EvalPage";
import { ExplorerPage } from "../features/explorer/ExplorerPage";
import { TracesPage } from "../features/traces/TracesPage";
import { VersionDetailPage } from "../features/ontology/VersionDetailPage";
import { VersionsPage } from "../features/ontology/VersionsPage";
import { SourcesPage } from "../features/sources/SourcesPage";
import { SessionPage } from "../features/workshop/SessionPage";
import { WorkshopPage } from "../features/workshop/WorkshopPage";
import { LoginPage, RequireAuth } from "./auth";
import { Layout } from "./Layout";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:conversationId" element={<ChatPage />} />
        <Route path="/explore" element={<ExplorerPage />} />
        <Route path="/traces" element={<TracesPage />} />
        <Route path="/traces/:traceId" element={<TracesPage />} />
        <Route path="/ontology" element={<VersionsPage />} />
        <Route path="/ontology/versions/:id" element={<VersionDetailPage />} />
        <Route path="/sources" element={<SourcesPage />} />
        <Route path="/actions" element={<ActionsPage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/workshop" element={<WorkshopPage />} />
        <Route path="/eval" element={<EvalPage />} />
        <Route path="/eval/:setId" element={<EvalPage />} />
        <Route path="/eval/:setId/runs/:runId" element={<EvalPage />} />
        <Route path="/workshop/:sessionId" element={<SessionPage />} />
      </Route>
    </Routes>
  );
}
