import { useEffect, useState } from "react";
import { Alias, api } from "../api";
import { AccountSelect, useMeta } from "../meta";

type Check = { ok: boolean; version?: string; api_version?: string; error?: string };

export default function SettingsPage() {
  const { meta, reload, accountName } = useMeta();
  const [defaultAcc, setDefaultAcc] = useState<string | null>(null);
  const [aliases, setAliases] = useState<Alias[]>([]);
  const [check, setCheck] = useState<Check | null>(null);
  const [draft, setDraft] = useState<{ kind: Alias["kind"]; alias: string; value: string }>({ kind: "account", alias: "", value: "" });
  const [saved, setSaved] = useState(false);

  const loadAliases = () => api.get<Alias[]>("/aliases").then(setAliases);
  useEffect(() => {
    api.get<{ default_account_id: string | null }>("/settings").then((s) => setDefaultAcc(s.default_account_id));
    loadAliases();
    api.get<Check>("/firefly/check").then(setCheck);
  }, []);

  async function saveDefault(v: string | null) {
    setDefaultAcc(v);
    await api.put("/settings", { default_account_id: v });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  }

  async function addAlias() {
    await api.post("/aliases", draft);
    setDraft({ ...draft, alias: "", value: "" });
    loadAliases();
  }

  return (
    <div className="stack">
      <section className="card">
        <h2>Firefly</h2>
        {check ? (
          check.ok ? (
            <p>
              <span className="pill ok">connesso</span> Firefly {check.version} · API {check.api_version}
            </p>
          ) : (
            <div className="alert err">Connessione fallita: {check.error}</div>
          )
        ) : (
          <p className="muted">Verifica…</p>
        )}
        <button className="ghost" onClick={() => reload(true)}>Ricarica conti e categorie</button>
        {meta && (
          <span className="muted small">
            {" "}
            {meta.accounts.length} conti · {meta.categories.length} categorie · {meta.budgets.length} budget
          </span>
        )}
      </section>

      <section className="card">
        <h2>Conto predefinito</h2>
        <p className="muted small">Usato per l'inserimento rapido senza @conto e per i profili senza conto associato.</p>
        <AccountSelect value={defaultAcc} onChange={saveDefault} placeholder="nessuno" /> {saved && <span className="pill ok">salvato</span>}
      </section>

      <section className="card">
        <h2>Alias per l'inserimento rapido</h2>
        <p className="muted small">
          <code>@carta</code> → conto, <code>#bar</code> → categoria. Senza alias viene cercato il nome esatto o l'inizio del nome in Firefly.
        </p>
        <table className="grid">
          <tbody>
            {aliases.map((a) => (
              <tr key={a.id}>
                <td>
                  <code>
                    {a.kind === "account" ? "@" : "#"}
                    {a.alias}
                  </code>
                </td>
                <td>→ {a.kind === "account" ? accountName(meta?.accounts.find((x) => x.name === a.value)?.id ?? null) || a.value : a.value}</td>
                <td className="actions">
                  <button
                    className="ghost small"
                    onClick={async () => {
                      await api.del(`/aliases/${a.id}`);
                      loadAliases();
                    }}
                  >
                    Elimina
                  </button>
                </td>
              </tr>
            ))}
            <tr>
              <td>
                <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as Alias["kind"], value: "" })}>
                  <option value="account">@ conto</option>
                  <option value="category"># categoria</option>
                </select>
                <input placeholder="alias" value={draft.alias} onChange={(e) => setDraft({ ...draft, alias: e.target.value.replace(/^[@#]/, "") })} />
              </td>
              <td>
                {draft.kind === "account" ? (
                  <select value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value })}>
                    <option value="">—</option>
                    {meta?.accounts.map((a) => (
                      <option key={a.id} value={a.name}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input list="dl-categories" placeholder="categoria" value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value })} />
                )}
              </td>
              <td>
                <button className="primary small" disabled={!draft.alias || !draft.value} onClick={addAlias}>
                  Aggiungi
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  );
}
