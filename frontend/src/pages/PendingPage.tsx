import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Batch, fmtAmount } from "../api";
import { useMeta } from "../meta";
import { notifyPending } from "../pending";
import PreviewModal, { Preview } from "./PreviewModal";

type Pending = { approved: Batch[]; review: Batch[] };
type Result = { imported: number; duplicate: number; error: number };

const label = (b: Batch) => b.filename ?? "inserimento rapido";

export default function PendingPage() {
  const { reload: reloadMeta } = useMeta();
  const [data, setData] = useState<Pending | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const load = useCallback(() => api.get<Pending>("/pending").then(setData), []);
  useEffect(() => {
    load().catch((e) => setMsg({ kind: "err", text: e.message }));
  }, [load]);

  async function run<T>(fn: () => Promise<T>) {
    setBusy(true);
    setMsg(null);
    try {
      return await fn();
    } catch (e) {
      setMsg({ kind: "err", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const openPreview = () => run(async () => setPreview(await api.post<Preview>("/pending/preview")));

  const confirm = (rowIds: number[]) =>
    run(async () => {
      const res = await api.post<Result>("/pending/import", { row_ids: rowIds });
      setPreview(null);
      setMsg({
        kind: res.error ? "err" : "ok",
        text:
          `Inviate a Firefly ${res.imported}` +
          (res.duplicate ? ` · già presenti/duplicate ${res.duplicate}` : "") +
          (res.error ? ` · errori ${res.error}: i lotti con errori restano qui, aprili per correggere e riprova` : ""),
      });
      await load();
      notifyPending();
      reloadMeta(true);
    });

  const reopen = (b: Batch) =>
    run(async () => {
      await api.post(`/batches/${b.id}/reopen`);
      await load();
      notifyPending();
    });

  if (!data) return msg ? <div className="alert err">{msg.text}</div> : <p className="muted">Caricamento…</p>;

  const rows = data.approved.reduce((n, b) => n + b.selected, 0);
  const total = data.approved.reduce((n, b) => n + parseFloat(b.selected_total), 0);

  return (
    <div className="stack">
      <section className="card">
        <div className="row-between">
          <div>
            <h2>Da inviare a Firefly</h2>
            <p className="muted small">
              Le righe dei lotti approvati. Nulla viene creato in Firefly finché non confermi dall'anteprima.
            </p>
          </div>
          <button className="primary" disabled={busy || !rows} onClick={openPreview}>
            {busy ? "Attendi…" : `Anteprima di tutto (${rows})`}
          </button>
        </div>
        {msg && <div className={`alert ${msg.kind}`}>{msg.text}</div>}
        {data.review.length > 0 && (
          <div className="alert warn">
            Ancora in revisione, non verranno inviati:{" "}
            {data.review.map((b, i) => (
              <span key={b.id}>
                {i > 0 && ", "}
                <Link to={`/batches/${b.id}`}>
                  #{b.id} {label(b)}
                </Link>{" "}
                ({b.selected})
              </span>
            ))}
          </div>
        )}
      </section>

      <section className="card">
        {!data.approved.length ? (
          <p className="muted">
            Nessun lotto approvato. Carica i file da <Link to="/">Importa</Link>, rivedi ogni lotto e premi «Approva».
          </p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>Lotto</th>
                <th className="num">Righe</th>
                <th className="num">Saldo</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.approved.map((b) => (
                <tr key={b.id}>
                  <td>
                    <Link to={`/batches/${b.id}`}>
                      #{b.id} {label(b)}
                    </Link>
                    <div className="small muted">{new Date(b.created_at).toLocaleString("it-IT")}</div>
                  </td>
                  <td className="num">{b.selected}</td>
                  <td className={`num nowrap ${parseFloat(b.selected_total) < 0 ? "neg" : "pos"}`}>{fmtAmount(b.selected_total)}</td>
                  <td className="nowrap">
                    <div className="actions">
                      <button className="ghost small" disabled={busy} onClick={() => reopen(b)}>
                        Riapri
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              <tr>
                <td>
                  <b>Totale</b>
                </td>
                <td className="num">
                  <b>{rows}</b>
                </td>
                <td className={`num nowrap ${total < 0 ? "neg" : "pos"}`}>
                  <b>{fmtAmount(total)}</b>
                </td>
                <td></td>
              </tr>
            </tbody>
          </table>
        )}
      </section>

      {preview && <PreviewModal preview={preview} busy={busy} onConfirm={confirm} onCancel={() => setPreview(null)} />}
    </div>
  );
}
