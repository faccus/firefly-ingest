import { useEffect, useState } from "react";
import { api, Profile } from "../api";
import { AccountSelect, useMeta } from "../meta";

function describeColumns(p: Profile): string {
  const c = p.columns;
  const amount = c.amount ? `importo: ${c.amount}` : `uscite: ${c.debit ?? "—"} / entrate: ${c.credit ?? "—"}`;
  return [`data: ${c.date}`, amount, `descrizione: ${c.description.join(" + ") || "—"}`, c.counterparty && `controparte: ${c.counterparty}`]
    .filter(Boolean)
    .join(" · ");
}

export default function ProfilesPage() {
  const { accountName } = useMeta();
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [editing, setEditing] = useState<Profile | null>(null);
  const load = () => api.get<Profile[]>("/profiles").then(setProfiles);
  useEffect(() => {
    load();
  }, []);

  async function save() {
    if (!editing) return;
    await api.put(`/profiles/${editing.id}`, { ...editing, invert_sign: !!editing.invert_sign });
    setEditing(null);
    load();
  }

  async function remove(p: Profile) {
    if (!confirm(`Eliminare il profilo "${p.name}"?`)) return;
    await api.del(`/profiles/${p.id}`);
    load();
  }

  return (
    <section className="card">
      <h2>Profili banca</h2>
      <p className="muted small">
        Un profilo descrive il formato di export di una banca. Si crea in automatico la prima volta che carichi un file non riconosciuto. Per
        rimappare le colonne elimina il profilo e ricarica un file.
      </p>
      {!profiles.length && <p className="muted">Nessun profilo: carica un estratto conto per crearne uno.</p>}
      <table className="grid">
        <tbody>
          {profiles.map((p) =>
            editing?.id === p.id ? (
              <tr key={p.id}>
                <td colSpan={3}>
                  <div className="fgrid">
                    <label>
                      Nome
                      <input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
                    </label>
                    <label>
                      Conto Firefly
                      <AccountSelect value={editing.account_id} onChange={(v) => setEditing({ ...editing, account_id: v })} placeholder="predefinito" />
                    </label>
                    <label>
                      Separatore decimale
                      <select
                        value={editing.decimal_sep ?? ""}
                        onChange={(e) => setEditing({ ...editing, decimal_sep: (e.target.value || null) as Profile["decimal_sep"] })}
                      >
                        <option value="">automatico</option>
                        <option value=",">virgola</option>
                        <option value=".">punto</option>
                      </select>
                    </label>
                    <label>
                      Formato data
                      <input value={editing.date_format ?? ""} placeholder="automatico" onChange={(e) => setEditing({ ...editing, date_format: e.target.value || null })} />
                    </label>
                    <label className="check">
                      <input type="checkbox" checked={!!editing.invert_sign} onChange={(e) => setEditing({ ...editing, invert_sign: e.target.checked })} /> inverti
                      segno
                    </label>
                  </div>
                  {editing.columns.account && (
                    <div className="subsection">
                      <b>Conto Firefly per "{editing.columns.account}"</b>
                      <table className="map-table">
                        <tbody>
                          {Object.keys(editing.options?.account_map ?? {}).map((v) => (
                            <tr key={v}>
                              <td>
                                <code>{v}</code>
                              </td>
                              <td>
                                <AccountSelect
                                  value={editing.options?.account_map?.[v]}
                                  placeholder="conto del profilo"
                                  onChange={(id) =>
                                    setEditing({
                                      ...editing,
                                      options: { ...editing.options, account_map: { ...editing.options?.account_map, [v]: id } },
                                    })
                                  }
                                />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <p className="muted small">
                        Valori non mappati (es. una carta nuova) finiscono sul conto del profilo. Per aggiungerne uno usa <code>*</code> come
                        ripiego, oppure ricrea il profilo.
                      </p>
                    </div>
                  )}
                  {editing.columns.category && (
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={!!editing.options?.use_bank_category}
                        onChange={(e) => setEditing({ ...editing, options: { ...editing.options, use_bank_category: e.target.checked } })}
                      />{" "}
                      usa la categoria della banca come ripiego
                    </label>
                  )}
                  <div className="actions end">
                    <button className="ghost" onClick={() => setEditing(null)}>Annulla</button>
                    <button className="primary" onClick={save}>Salva</button>
                  </div>
                </td>
              </tr>
            ) : (
              <tr key={p.id}>
                <td>
                  <b>{p.name}</b>
                  <div className="small muted">{describeColumns(p)}</div>
                </td>
                <td>{accountName(p.account_id)}</td>
                <td className="actions">
                  <button className="ghost small" onClick={() => setEditing({ ...p })}>Modifica</button>
                  <button className="ghost small" onClick={() => remove(p)}>Elimina</button>
                </td>
              </tr>
            ),
          )}
        </tbody>
      </table>
    </section>
  );
}
