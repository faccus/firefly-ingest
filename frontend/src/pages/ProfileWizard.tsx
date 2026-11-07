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
  const [accountMap, setAccountMap] = useState<Record<string, string | null>>({});
  const [statusSkip, setStatusSkip] = useState<string[]>([]);
  const [useBankCat, setUseBankCat] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<Inspect>(`/uploads/${uploadId}/inspect`)
      .then((i) => {
        setInsp(i);
        setCols(i.guess);
        if (i.guess.status) setStatusSkip((i.distinct[i.guess.status] ?? []).filter((v) => /^non\b|^in attesa|^pending/i.test(v)));
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

  const accountValues = cols.account ? insp.distinct[cols.account] ?? [] : [];
  const statusValues = cols.status ? insp.distinct[cols.status] ?? [] : [];
  const valid =
    !!name.trim() &&
    !!cols.date &&
    (mode === "single" ? !!cols.amount : !!(cols.debit || cols.credit)) &&
    (!cols.account || accountValues.every((v) => accountMap[v]) || !!account);

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
        options: {
          account_map: cols!.account ? accountMap : undefined,
          status_skip: cols!.status ? statusSkip : undefined,
          use_bank_category: cols!.category ? useBankCat : false,
        },
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
            {colSelect("account", "Conto/carta per riga")}
            {colSelect("status", "Stato contabile")}
            {colSelect("category", "Categoria della banca")}
          </div>
          {cols.account && (
            <div className="subsection">
              <b>Conto Firefly per ogni valore di "{cols.account}"</b>
              <p className="muted small">Il file unisce più conti o carte: ogni riga va sul conto Firefly corrispondente.</p>
              <table className="map-table">
                <tbody>
                  {accountValues.map((v) => (
                    <tr key={v}>
                      <td>
                        <code>{v}</code>
                      </td>
                      <td>
                        <AccountSelect value={accountMap[v]} onChange={(id) => setAccountMap((m) => ({ ...m, [v]: id }))} placeholder="conto del profilo" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!accountValues.length && <p className="muted small">Nessun valore trovato in questa colonna.</p>}
            </div>
          )}
          {cols.status && (
            <div className="subsection">
              <b>Escludi le righe con stato</b>
              <div className="desc-cols">
                {statusValues.map((v) => (
                  <label key={v} className={`chip ${statusSkip.includes(v) ? "on" : ""}`}>
                    <input
                      type="checkbox"
                      checked={statusSkip.includes(v)}
                      onChange={() => setStatusSkip((s) => (s.includes(v) ? s.filter((x) => x !== v) : [...s, v]))}
                    />
                    {v}
                  </label>
                ))}
              </div>
              <p className="muted small">
                Utile per i movimenti "non contabilizzati": possono ancora cambiare, meglio importarli col file successivo.
              </p>
            </div>
          )}
          {cols.category && (
            <label className="check subsection">
              <input type="checkbox" checked={useBankCat} onChange={(e) => setUseBankCat(e.target.checked)} /> usa la categoria della banca
              quando né regole né storico ne propongono una (crea in Firefly le categorie con i nomi della banca)
            </label>
          )}
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
