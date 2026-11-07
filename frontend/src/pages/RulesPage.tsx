import { useEffect, useState } from "react";
import { api, Rule } from "../api";
import { useMeta } from "../meta";
import RuleForm, { emptyRule, RuleDraft } from "./RuleForm";
import Modal from "../Modal";

const MATCH: Record<Rule["match_type"], string> = { contains: "contiene", equals: "=", starts: "inizia con", regex: "regex" };
const FIELD: Record<Rule["field"], string> = { any: "testo", description: "descrizione", counterparty: "controparte", notes: "note" };

export default function RulesPage() {
  const { accountName } = useMeta();
  const [rules, setRules] = useState<Rule[]>([]);
  const [editing, setEditing] = useState<RuleDraft | null>(null);
  const [q, setQ] = useState("");
  const load = () => api.get<Rule[]>("/rules").then(setRules);
  useEffect(() => {
    load();
  }, []);

  async function remove(r: Rule) {
    if (!confirm(`Eliminare la regola "${r.name || r.pattern}"?`)) return;
    await api.del(`/rules/${r.id}`);
    load();
  }

  const shown = rules.filter((r) => !q || `${r.name} ${r.pattern} ${r.set_category}`.toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="stack">
      <section className="card">
        <div className="row-between">
          <h2>Regole ({rules.length})</h2>
          <div className="actions">
            <input placeholder="Cerca…" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className="primary" onClick={() => setEditing({ ...emptyRule })}>Nuova regola</button>
          </div>
        </div>
        <p className="muted small">
          Le regole sono applicate in ordine di priorità: ogni regola riempie solo i campi ancora vuoti. Le correzioni fatte nella revisione non vengono mai sovrascritte.
        </p>
        <table className="grid">
          <thead>
            <tr>
              <th>Prio</th>
              <th>Condizione</th>
              <th>Imposta</th>
              <th>Usata</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id} className={r.active ? "" : "inactive"}>
                <td>{r.priority}</td>
                <td>
                  <b>{r.name || r.pattern}</b>
                  <div className="small muted">
                    {FIELD[r.field]} {MATCH[r.match_type]} “{r.pattern}”
                    {r.direction !== "any" && ` · ${r.direction === "out" ? "uscite" : "entrate"}`}
                    {r.account_id && ` · ${accountName(r.account_id)}`}
                    {(r.amount_min != null || r.amount_max != null) && ` · ${r.amount_min ?? "…"}–${r.amount_max ?? "…"} €`}
                  </div>
                </td>
                <td className="small">
                  {[
                    r.set_category && `categoria: ${r.set_category}`,
                    r.set_budget && `budget: ${r.set_budget}`,
                    r.set_counterparty && `controparte: ${r.set_counterparty}`,
                    r.set_description && `descrizione: ${r.set_description}`,
                    r.set_tags?.length && `tag: ${r.set_tags.join(", ")}`,
                    r.set_transfer_account_id && `giroconto: ${accountName(r.set_transfer_account_id)}`,
                    r.set_skip && "ignora",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </td>
                <td>{r.hits}</td>
                <td className="actions">
                  <button className="ghost small" onClick={() => setEditing({ ...r })}>Modifica</button>
                  <button className="ghost small" onClick={() => remove(r)}>Elimina</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {editing && (
        <Modal onClose={() => setEditing(null)}>
            <h3>{editing.id ? "Modifica regola" : "Nuova regola"}</h3>
            <RuleForm
              initial={editing}
              onSaved={() => {
                setEditing(null);
                load();
              }}
              onCancel={() => setEditing(null)}
            />
        </Modal>
      )}
    </div>
  );
}
