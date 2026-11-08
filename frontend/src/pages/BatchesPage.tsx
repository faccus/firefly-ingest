import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Batch } from "../api";
import { StatusCounts } from "./BatchPage";

const SOURCE: Record<string, string> = { web: "Web", telegram: "Telegram" };
const STATUS: Record<Batch["status"], JSX.Element> = {
  review: <span className="pill">da rivedere</span>,
  approved: <span className="pill st-possible_duplicate">approvato, da inviare</span>,
  done: <span className="pill ok">inviato</span>,
};

export default function BatchesPage() {
  const [batches, setBatches] = useState<Batch[] | null>(null);
  useEffect(() => {
    api.get<Batch[]>("/batches").then(setBatches);
  }, []);
  if (!batches) return <p className="muted">Caricamento…</p>;
  if (!batches.length) return <p className="muted">Nessun lotto ancora.</p>;
  return (
    <section className="card">
      <h2>Lotti</h2>
      <table className="grid">
        <thead>
          <tr>
            <th>#</th>
            <th>Creato</th>
            <th>Origine</th>
            <th>File</th>
            <th>Stato</th>
            <th>Righe</th>
          </tr>
        </thead>
        <tbody>
          {batches.map((b) => (
            <tr key={b.id}>
              <td>
                <Link to={`/batches/${b.id}`}>{b.id}</Link>
              </td>
              <td>{new Date(b.created_at).toLocaleString("it-IT")}</td>
              <td>{SOURCE[b.source] ?? b.source}</td>
              <td>{b.filename ?? <span className="muted">testo</span>}</td>
              <td>{STATUS[b.status]}</td>
              <td>
                <StatusCounts counts={b.counts} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
