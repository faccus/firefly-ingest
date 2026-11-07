import { useState } from "react";
import { api, Rule } from "../api";
import { AccountSelect } from "../meta";

export type RuleDraft = Omit<Rule, "id" | "hits"> & { id?: number };

export const emptyRule: RuleDraft = {
  name: "",
  priority: 100,
  field: "any",
  match_type: "contains",
  pattern: "",
  direction: "any",
  account_id: null,
  amount_min: null,
  amount_max: null,
  set_category: null,
  set_budget: null,
  set_tags: null,
  set_counterparty: null,
  set_description: null,
  set_transfer_account_id: null,
  active: true,
};

export default function RuleForm({ initial, onSaved, onCancel }: { initial: RuleDraft; onSaved: () => void; onCancel: () => void }) {
  const [r, setR] = useState<RuleDraft>(initial);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof RuleDraft>(k: K, v: RuleDraft[K]) => setR((x) => ({ ...x, [k]: v }));
  const num = (s: string) => (s.trim() === "" ? null : parseFloat(s.replace(",", ".")));

  async function save() {
    setError(null);
    const body = { ...r, active: !!r.active };
    delete (body as { id?: number }).id;
    try {
      if (r.id) await api.put(`/rules/${r.id}`, body);
      else await api.post("/rules", body);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div className="form">
      <fieldset>
        <legend>Se…</legend>
        <div className="fgrid">
          <label>
            Campo
            <select value={r.field} onChange={(e) => set("field", e.target.value as RuleDraft["field"])}>
              <option value="any">descrizione o controparte</option>
              <option value="description">descrizione</option>
              <option value="counterparty">controparte</option>
              <option value="notes">note</option>
            </select>
          </label>
          <label>
            Corrispondenza
            <select value={r.match_type} onChange={(e) => set("match_type", e.target.value as RuleDraft["match_type"])}>
              <option value="contains">contiene</option>
              <option value="starts">inizia con</option>
              <option value="equals">uguale a</option>
              <option value="regex">regex</option>
            </select>
          </label>
          <label className="wide">
            Testo
            <input autoFocus value={r.pattern} onChange={(e) => set("pattern", e.target.value)} />
          </label>
          <label>
            Direzione
            <select value={r.direction} onChange={(e) => set("direction", e.target.value as RuleDraft["direction"])}>
              <option value="any">entrate e uscite</option>
              <option value="out">solo uscite</option>
              <option value="in">solo entrate</option>
            </select>
          </label>
          <label>
            Solo sul conto
            <AccountSelect value={r.account_id} onChange={(v) => set("account_id", v)} placeholder="qualsiasi" />
          </label>
          <label>
            Importo da
            <input inputMode="decimal" value={r.amount_min ?? ""} onChange={(e) => set("amount_min", num(e.target.value))} />
          </label>
          <label>
            Importo a
            <input inputMode="decimal" value={r.amount_max ?? ""} onChange={(e) => set("amount_max", num(e.target.value))} />
          </label>
        </div>
      </fieldset>
      <fieldset>
        <legend>…allora imposta</legend>
        <div className="fgrid">
          <label>
            Categoria
            <input list="dl-categories" value={r.set_category ?? ""} onChange={(e) => set("set_category", e.target.value || null)} />
          </label>
          <label>
            Budget
            <input list="dl-budgets" value={r.set_budget ?? ""} onChange={(e) => set("set_budget", e.target.value || null)} />
          </label>
          <label>
            Controparte
            <input value={r.set_counterparty ?? ""} onChange={(e) => set("set_counterparty", e.target.value || null)} placeholder="es. Esselunga" />
          </label>
          <label>
            Descrizione
            <input value={r.set_description ?? ""} onChange={(e) => set("set_description", e.target.value || null)} placeholder="lascia vuoto per non cambiarla" />
          </label>
          <label>
            Tag (virgola)
            <input
              list="dl-tags"
              value={(r.set_tags ?? []).join(", ")}
              onChange={(e) => set("set_tags", e.target.value ? e.target.value.split(",").map((t) => t.trim()).filter(Boolean) : null)}
            />
          </label>
          <label>
            Giroconto con
            <AccountSelect value={r.set_transfer_account_id} onChange={(v) => set("set_transfer_account_id", v)} placeholder="no" />
          </label>
        </div>
      </fieldset>
      <div className="fgrid">
        <label>
          Nome
          <input value={r.name ?? ""} onChange={(e) => set("name", e.target.value)} />
        </label>
        <label>
          Priorità <small className="muted">(più bassa = prima)</small>
          <input type="number" value={r.priority} onChange={(e) => set("priority", parseInt(e.target.value || "100", 10))} />
        </label>
        <label className="check">
          <input type="checkbox" checked={!!r.active} onChange={(e) => set("active", e.target.checked)} /> attiva
        </label>
      </div>
      {error && <div className="alert err">{error}</div>}
      <div className="actions end">
        <button className="ghost" onClick={onCancel}>Annulla</button>
        <button className="primary" disabled={!r.pattern.trim()} onClick={save}>Salva regola</button>
      </div>
    </div>
  );
}
