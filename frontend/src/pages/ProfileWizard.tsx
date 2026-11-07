import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, Columns, Inspect, Profile, UploadResult } from "../api";
import { AccountSelect } from "../meta";

type Mode = "single" | "split";

export default function ProfileWizard() {
  const { uploadId } = useParams();
  const nav = useNavigate();
  const [insp, setInsp] = useState<Inspect | null>(null);
  const [cols, setCols] = useState<Columns | null>(null);
  const [mode, setMode] = useState<Mode>("single");
  const [name, setName] = useState("");
  const [account, setAccount] = useState<string | null>(null);
  const [decimal, setDecimal] = useState<"" | "," | ".">("");
  const [dateFormat, setDateFormat] = useState("");
  const [invert, setInvert] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<Inspect>(`/uploads/${uploadId}/inspect`)
      .then((i) => {
        setInsp(i);
        setCols(i.guess);
        setMode(i.guess.amount || !(i.guess.debit || i.guess.credit) ? "single" : "split");
        setName(i.filename.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "));
      })
      .catch((e) => setError(e.message));
  }, [uploadId]);

  if (error && !insp) return <div className="alert err">{error}</div>;
  if (!insp || !cols) return <p className="muted">Analisi del file…</p>;

  const headers = insp.headers.filter(Boolean);
  const setCol = (k: keyof Columns, v: string | null) => setCols((c) => ({ ...c!, [k]: v }));
  const toggleDesc = (h: string) =>
    setCols((c) => ({ ...c!, description: c!.description.includes(h) ? c!.description.filter((x) => x !== h) : [...c!.description, h] }));

  const colSelect = (k: keyof Columns, label: string, required = false) => (
    <label>
      {label}
      {required && " *"}
      <select value={(cols[k] as string | null) ?? ""} onChange={(e) => setCol(k, e.target.value || null)}>
        <option value="">—</option>
        {headers.map((h) => (
          <option key={h} value={h}>
            {h}
          </option>
        ))}
      </select>
    </label>
  );

  const valid = !!name.trim() && !!cols.date && (mode === "single" ? !!cols.amount : !!(cols.debit || cols.credit));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const columns: Columns = mode === "single" ? { ...cols!, debit: null, credit: null } : { ...cols!, amount: null };
      const p = await api.post<Profile>("/profiles", {
        name: name.trim(),
        account_id: account,
        decimal_sep: decimal || null,
        date_format: dateFormat || null,
        invert_sign: invert,
        encoding: insp!.encoding,
        delimiter: insp!.delimiter,
        columns,
      });
      const res = await api.post<UploadResult>(`/uploads/${uploadId}/process`, { profile_id: p.id, account_id: account });
      if ("batch_id" in res) nav(`/batches/${res.batch_id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const highlight = new Set<string>([cols.date, cols.amount, cols.debit, cols.credit, cols.counterparty, cols.notes, ...cols.description].filter(Boolean) as string[]);

  return (
    <div className="stack">
      <section className="card">
        <h2>Nuovo profilo banca</h2>
        <p className="muted">
          Non ho riconosciuto il formato di <b>{insp.filename}</b>. Indica quali colonne usare: la prossima volta i file di questa banca verranno
          riconosciuti in automatico dalle intestazioni.
        </p>
        <div className="fgrid">
          <label className="wide">
            Nome profilo *
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="es. Fineco conto, Amex…" />
          </label>
          <label>
            Conto Firefly
            <AccountSelect value={account} onChange={setAccount} placeholder="usa il predefinito" />
          </label>
        </div>
        <fieldset>
          <legend>Colonne</legend>
          <div className="fgrid">
            {colSelect("date", "Data", true)}
            <label>
              Importo
              <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
                <option value="single">un'unica colonna con segno</option>
                <option value="split">colonne separate uscite / entrate</option>
              </select>
            </label>
            {mode === "single" ? colSelect("amount", "Colonna importo", true) : (
              <>
                {colSelect("debit", "Uscite (addebiti)")}
                {colSelect("credit", "Entrate (accrediti)")}
              </>
            )}
            {colSelect("counterparty", "Controparte")}
            {colSelect("notes", "Note")}
          </div>
          <div className="desc-cols">
            Descrizione (anche più colonne, unite nell'ordine):
            {headers.map((h) => (
              <label key={h} className={`chip ${cols.description.includes(h) ? "on" : ""}`}>
                <input type="checkbox" checked={cols.description.includes(h)} onChange={() => toggleDesc(h)} />
                {cols.description.includes(h) && <b>{cols.description.indexOf(h) + 1}</b>} {h}
              </label>
            ))}
          </div>
        </fieldset>
        <details>
          <summary>Opzioni avanzate</summary>
          <div className="fgrid">
            <label>
              Separatore decimale
              <select value={decimal} onChange={(e) => setDecimal(e.target.value as "" | "," | ".")}>
                <option value="">automatico</option>
                <option value=",">virgola (1.234,56)</option>
                <option value=".">punto (1,234.56)</option>
              </select>
            </label>
            <label>
              Formato data
              <input value={dateFormat} onChange={(e) => setDateFormat(e.target.value)} placeholder="automatico, es. %d/%m/%Y" />
            </label>
            <label className="check">
              <input type="checkbox" checked={invert} onChange={(e) => setInvert(e.target.checked)} /> inverti segno (es. carte di credito con
              spese positive)
            </label>
          </div>
        </details>
        {error && <div className="alert err">{error}</div>}
        <div className="actions end">
          <button className="primary" disabled={!valid || busy} onClick={save}>
            Salva profilo e continua
          </button>
        </div>
      </section>

      <section className="card tablewrap">
        <h3>Anteprima ({insp.delimiter ? `CSV "${insp.delimiter}"` : "Excel"}, intestazione alla riga {insp.header_row + 1})</h3>
        <table className="grid sample">
          <thead>
            <tr>
              {insp.headers.map((h, i) => (
                <th key={i} className={highlight.has(h) ? "hl" : ""}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {insp.sample.map((r, i) => (
              <tr key={i}>
                {insp.headers.map((h, j) => (
                  <td key={j} className={highlight.has(h) ? "hl" : ""}>
                    {r[j]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
