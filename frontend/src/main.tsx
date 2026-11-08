import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api";
import { onPendingChange } from "./pending";
import PendingPage from "./pages/PendingPage";
import { MetaDatalists, MetaProvider, useMeta } from "./meta";
import ImportPage from "./pages/ImportPage";
import BatchesPage from "./pages/BatchesPage";
import BatchPage from "./pages/BatchPage";
import RulesPage from "./pages/RulesPage";
import ProfilesPage from "./pages/ProfilesPage";
import ProfileWizard from "./pages/ProfileWizard";
import SettingsPage from "./pages/SettingsPage";
import "./styles.css";

function FireflyStatus() {
  const { meta } = useMeta();
  if (!meta) return <span className="pill">…</span>;
  return meta.firefly_ok ? (
    <span className="pill ok" title="Connesso a Firefly">Firefly ●</span>
  ) : (
    <span className="pill err" title={meta.error ?? ""}>Firefly non raggiungibile</span>
  );
}

function PendingLink() {
  const [n, setN] = useState(0);
  const { pathname } = useLocation();
  useEffect(() => {
    const load = () => api.get<{ rows: number }>("/pending/count").then((r) => setN(r.rows)).catch(() => {});
    load();
    return onPendingChange(load);
  }, [pathname]);
  return (
    <NavLink to="/pending">
      Da inviare {n > 0 && <span className="pill badge">{n}</span>}
    </NavLink>
  );
}

function App() {
  return (
    <MetaProvider>
      <MetaDatalists />
      <header className="topbar">
        <span className="brand">📥 Firefly Ingest</span>
        <nav>
          <NavLink to="/" end>Importa</NavLink>
          <NavLink to="/batches">Lotti</NavLink>
          <PendingLink />
          <NavLink to="/rules">Regole</NavLink>
          <NavLink to="/profiles">Profili</NavLink>
          <NavLink to="/settings">Impostazioni</NavLink>
        </nav>
        <FireflyStatus />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<ImportPage />} />
          <Route path="/batches" element={<BatchesPage />} />
          <Route path="/batches/:id" element={<BatchPage />} />
          <Route path="/pending" element={<PendingPage />} />
          <Route path="/rules" element={<RulesPage />} />
          <Route path="/profiles" element={<ProfilesPage />} />
          <Route path="/uploads/:uploadId/profile" element={<ProfileWizard />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </MetaProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
