import { Navigate, Route, Routes } from "react-router-dom";

import { VersionDetailPage } from "../features/ontology/VersionDetailPage";
import { VersionsPage } from "../features/ontology/VersionsPage";
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
        <Route index element={<Navigate to="/ontology" replace />} />
        <Route path="/ontology" element={<VersionsPage />} />
        <Route path="/ontology/versions/:id" element={<VersionDetailPage />} />
      </Route>
    </Routes>
  );
}
