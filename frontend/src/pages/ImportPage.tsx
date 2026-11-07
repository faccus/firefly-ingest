import { DragEvent, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, UploadResult } from "../api";
import { AccountSelect } from "../meta";

export default function ImportPage() {
  const nav = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const [account, setAccount] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      if (account) fd.append("account_id", account);
      const res = await api.post<UploadResult>("/upload", fd);
      if ("needs_profile" in res) nav(`/uploads/${res.upload_id}/profile`);
      else nav(`/batches/${res.batch_id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function submitText() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<{ batch_id: number }>("/text", { text, account_id: account });
      nav(`/batches/${res.batch_id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDrag(false);
    const f = e.dataTransfer.files[0];
    if (f) upload(f);
  }

  return (
    <div className="stack">
      <section className="card">
        <h2>Estratto conto</h2>
        <div
          className={`dropzone ${drag ? "drag" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}
          onClick={() => fileRef.current?.click()}
        >
          {busy ? "Elaborazione…" : "Trascina qui un CSV / XLSX / XLS della banca, oppure clicca per sceglierlo"}
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.txt,.xlsx,.xlsm,.xls"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) upload(f);
              e.target.value = "";
            }}
          />
        </div>
        <label className="inline">
          Conto Firefly
          <AccountSelect value={account} onChange={setAccount} placeholder="dal profilo della banca" />
        </label>
      </section>

      <section className="card">
        <h2>Inserimento rapido</h2>
        <textarea
          rows={4}
          placeholder={"12.50 pizza ieri @carta #cene\n+1800 stipendio 27/10\n3,20 caffè e brioche"}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && text.trim()) submitText();
          }}
        />
        <div className="row-between">
          <small className="muted">
            Una transazione per riga · <b>+</b> entrata · oggi/ieri/lunedì/3/11 · <b>@conto</b> <b>#categoria</b> <b>!tag</b> · Ctrl+Invio
          </small>
          <button className="primary" disabled={busy || !text.trim()} onClick={submitText}>
            Anteprima
          </button>
        </div>
      </section>

      {error && <div className="alert err">{error}</div>}
    </div>
  );
}
