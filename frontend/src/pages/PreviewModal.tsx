import { fmtAmount, fmtDate } from "../api";
import Modal from "../Modal";

export type PreviewItem = {
  row_id: number;
  batch_id: number;
  batch: string;
  creates: boolean;
  type?: "withdrawal" | "deposit" | "transfer";
  type_label?: string;
  date: string;
  amount: string;
  source?: string;
  destination?: string;
  description: string;
  category?: string | null;
  budget?: string | null;
  tags?: string[];
  notes?: string | null;
  forced?: boolean;
  warnings: string[];
  errors: string[];
};

export type Preview = {
  items: PreviewItem[];
  summary: { count: number; by_type: Record<string, { count: number; total: string }>; errors: number; warnings: number; not_created: number };
  batches: { id: number; label: string }[];
  new_categories: string[];
  new_tags: string[];
  new_counterparties: string[];
  firefly_ok: boolean;
};

const SIGN: Record<string, number> = { withdrawal: -1, deposit: 1, transfer: 0 };
const plain = (a: string) => parseFloat(a).toLocaleString("it-IT", { style: "currency", currency: "EUR" });

export default function PreviewModal({
  preview,
  busy,
  onConfirm,
  onCancel,
}: {
  preview: Preview;
  busy: boolean;
  /** Senza onConfirm è solo un'anteprima (es. di un singolo lotto). */
  onConfirm?: (rowIds: number[]) => void;
  onCancel: () => void;
}) {
  // si inviano anche le contropartite dei giroconti: il server le chiude senza creare nulla
  const sendable = preview.items.filter((i) => !i.errors.length);
  const { by_type } = preview.summary;
  const multi = preview.batches.length > 1;
  return (
    <Modal onClose={onCancel} wide>
        <h3>
          {onConfirm ? "Verranno create" : "Questo lotto creerà"} {preview.summary.count} transazioni in Firefly
          {multi && <small className="muted"> da {preview.batches.length} lotti</small>}
        </h3>
        <div className="counts">
          {Object.entries(by_type).map(([label, v]) => (
            <span key={label} className="pill">
              {v.count} {label} · {plain(v.total)}
            </span>
          ))}
          {preview.summary.not_created > 0 && <span className="pill">{preview.summary.not_created} giroconti già coperti</span>}
          {preview.summary.warnings > 0 && <span className="pill st-possible_duplicate">{preview.summary.warnings} con avvisi</span>}
          {preview.summary.errors > 0 && <span className="pill st-error">{preview.summary.errors} non inviabili</span>}
        </div>

        {!preview.firefly_ok && <div className="alert err">Firefly non raggiungibile: l'invio fallirà.</div>}
        {(preview.new_categories.length > 0 || preview.new_tags.length > 0 || preview.new_counterparties.length > 0) && (
          <div className="alert warn">
            Saranno creati in Firefly:
            {preview.new_categories.length > 0 && (
              <div>
                <b>categorie</b> {preview.new_categories.join(", ")}
              </div>
            )}
            {preview.new_tags.length > 0 && (
              <div>
                <b>tag</b> {preview.new_tags.join(", ")}
              </div>
            )}
            {preview.new_counterparties.length > 0 && (
              <div>
                <b>controparti</b> {preview.new_counterparties.join(", ")}
              </div>
            )}
          </div>
        )}

        <div className="tablewrap preview-table">
          <table className="grid">
            <thead>
              <tr>
                <th>Data</th>
                {multi && <th>Lotto</th>}
                <th>Tipo</th>
                <th>Da → a</th>
                <th className="num">Importo</th>
                <th>Descrizione</th>
                <th>Categoria / budget / tag</th>
                <th>Avvisi</th>
              </tr>
            </thead>
            <tbody>
              {preview.items.map((i) => (
                <tr
                  key={i.row_id}
                  className={i.errors.length ? "st-error" : !i.creates ? "inactive" : i.warnings.length ? "st-possible_duplicate" : ""}
                >
                  <td className="nowrap">{fmtDate(i.date)}</td>
                  {multi && <td className="small muted nowrap">{i.batch}</td>}
                  <td>{i.type_label ?? "—"}</td>
                  <td>
                    {i.source ?? "?"} <span className="muted">→</span> {i.destination ?? "?"}
                  </td>
                  <td className={`num nowrap ${i.type ? (SIGN[i.type] < 0 ? "neg" : SIGN[i.type] > 0 ? "pos" : "") : ""}`}>
                    {i.type && i.type !== "transfer" ? fmtAmount(SIGN[i.type] * parseFloat(i.amount)) : plain(i.amount)}
                  </td>
                  <td>{i.description}</td>
                  <td className="small">
                    {[i.category, i.budget && `budget: ${i.budget}`, i.tags?.length && `tag: ${i.tags.join(", ")}`].filter(Boolean).join(" · ") || (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="small">
                    {[...i.errors.map((e) => `❌ ${e}`), ...i.warnings.map((w) => `⚠️ ${w}`)].map((t) => (
                      <div key={t}>{t}</div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="actions end">
          <button className={onConfirm ? "ghost" : "primary"} onClick={onCancel}>
            {onConfirm ? "Annulla" : "Chiudi"}
          </button>
          {onConfirm && (
            <button className="primary" disabled={busy || !sendable.length} onClick={() => onConfirm(sendable.map((i) => i.row_id))}>
              {busy ? "Invio…" : `Conferma e invia ${preview.summary.count} a Firefly`}
            </button>
          )}
        </div>
    </Modal>
  );
}
