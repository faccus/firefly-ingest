import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Batch, fmtAmount, fmtDate, Row, RowStatus } from "../api";
import { AccountSelect, useMeta } from "../meta";
import RuleForm, { emptyRule } from "./RuleForm";
import PreviewModal, { Preview } from "./PreviewModal";
import Modal from "../Modal";
import { notifyPending } from "../pending";

const STATUS_LABEL: Record<RowStatus, string> = {
  new: "pronta",
  duplicate: "duplicata",
  possible_duplicate: "possibile duplicato",
  skipped: "esclusa",
  imported: "importata",
  error: "errore",
};

export function StatusCounts({ counts }: { counts: Batch["counts"] }) {
  return (
    <span className="counts">
      {(Object.keys(STATUS_LABEL) as RowStatus[])
        .filter((s) => counts[s])
        .map((s) => (
          <span key={s} className={`pill st-${s}`}>
            {counts[s]} {STATUS_LABEL[s]}
          </span>
        ))}
    </span>
  );
}

type Filter = "all" | "todo" | "nocat" | "dup";

function suggestPattern(r: Row): string {
  if (r.counterparty) return r.counterparty;
  const words = r.description
    .replace(/\d{2}[/.-]\d{2}([/.-]\d{2,4})?/g, " ")
    .split(/\s+/)
    .filter((w) => w.length > 2 && !/^\d+([.,]\d+)?$/.test(w) && !/^(pagamento|pos|carta|bonifico|del|presso|ore|sdd|addebito)$/i.test(w));
  return words.slice(0, 2).join(" ") || r.description;
}

export default function BatchPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { meta } = useMeta();
  const [batch, setBatch] = useState<Batch | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [ruleFor, setRuleFor] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [bulkCat, setBulkCat] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);

  const load = useCallback(async () => {
    setBatch(await api.get<Batch>(`/batches/${id}`));
  }, [id]);

  useEffect(() => {
    load().catch((e) => setMsg({ kind: "err", text: e.message }));
  }, [load]);

  const approved = batch?.status === "approved";

  const rows = batch?.rows ?? [];
  const visible = useMemo(() => {
    switch (filter) {
      case "todo":
        return rows.filter((r) => !["imported", "duplicate"].includes(r.status));
      case "nocat":
        return rows.filter((r) => !r.category && !r.transfer_account_id && r.status !== "imported");
      case "dup":
        return rows.filter((r) => r.status === "duplicate" || r.status === "possible_duplicate");
      default:
        return rows;
    }
  }, [rows, filter]);

  const selectable = (r: Row) => r.status !== "imported" && !approved;
  const selectedRows = rows.filter((r) => r.selected && r.status !== "imported");
  const total = selectedRows.reduce((s, r) => s + parseFloat(r.amount), 0);

  // la selezione è salvata sul server: è ciò che verrà inviato quando il lotto è approvato
  async function setSelection(ids: number[], value: boolean) {
    if (!ids.length) return;
    const set = new Set(ids);
    setBatch((b) => (b ? { ...b, rows: b.rows!.map((x) => (set.has(x.id) ? { ...x, selected: value } : x)) } : b));
    try {
      await api.post(`/batches/${id}/selection`, { row_ids: ids, selected: value });
    } catch (e) {
      setMsg({ kind: "err", text: (e as Error).message });
      load();
    }
  }

  async function patch(r: Row, values: Partial<Row>) {
    const updated = await api.patch<Row>(`/rows/${r.id}`, values);
    setBatch((b) => (b ? { ...b, rows: b.rows!.map((x) => (x.id === r.id ? updated : x)) } : b));
  }

  async function bulkCategory() {
    for (const r of selectedRows) await patch(r, { category: bulkCat || null });
    setBulkCat("");
  }

  async function openPreview() {
    setBusy(true);
    setMsg(null);
    try {
      setPreview(await api.post<Preview>(`/batches/${id}/preview`));
    } catch (e) {
      setMsg({ kind: "err", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(action: "approve" | "reopen") {
    setBusy(true);
    setMsg(null);
    try {
      await api.post(`/batches/${id}/${action}`);
      await load();
      notifyPending();
    } catch (e) {
      setMsg({ kind: "err", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function deleteBatch() {
    if (!confirm("Eliminare questo lotto? (non tocca nulla in Firefly)")) return;
    await api.del(`/batches/${id}`);
    nav("/batches");
  }

  async function reapplyRules(prefix = "") {
    setBusy(true);
    try {
      const res = await api.post<{ changed: number; suggested: number }>(`/batches/${id}/reapply-rules`);
      const parts = [`${res.changed} righe aggiornate dalle regole`];
      if (res.suggested) parts.push(`${res.suggested} categorie suggerite dallo storico`);
      setMsg({ kind: "ok", text: prefix + parts.join(" · ") });
      await load();
    } catch (e) {
      setMsg({ kind: "err", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function afterRuleSaved() {
    setRuleFor(null);
    await reapplyRules("Regola salvata · ");
  }

  if (!batch) return msg ? <div className="alert err">{msg.text}</div> : <p className="muted">Caricamento…</p>;

  return (
    <div className="stack">
      <section className="card">
        <div className="row-between">
          <div>
            <h2>
              Lotto #{batch.id} <small className="muted">{batch.filename ?? "inserimento rapido"}</small>
            </h2>
            <StatusCounts counts={batch.counts} />
          </div>
          <div className="actions">
            <button onClick={deleteBatch} className="ghost">Elimina lotto</button>
            {approved ? (
              <button onClick={() => setStatus("reopen")} disabled={busy}>Riapri per modificare</button>
            ) : (
              <>
                <button
                  onClick={() => reapplyRules()}
                  disabled={busy}
                  title="Ricalcola le righe con le regole attuali. Le modifiche fatte a mano restano; svuota un campo per restituirlo alle regole."
                >
                  ↻ Riapplica regole
                </button>
                <button disabled={busy || !selectedRows.length} onClick={openPreview} title="Cosa verrà creato in Firefly da questo lotto (non invia nulla)">
                  Anteprima
                </button>
                <button
                  className="primary"
                  disabled={busy || !selectedRows.length}
                  onClick={() => setStatus("approve")}
                  title="Mette in coda le righe selezionate. L'invio a Firefly si conferma dalla pagina Da inviare, insieme agli altri lotti."
                >
                  {busy ? "Attendi…" : `Approva ${selectedRows.length}`}
                  {selectedRows.length > 0 && <small> ({fmtAmount(total)})</small>}
                </button>
              </>
            )}
          </div>
        </div>
        {approved && (
          <div className="alert ok">
            Lotto approvato: {selectedRows.length} righe in coda. Quando hai approvato tutti i lotti, conferma l'invio da{" "}
            <Link to="/pending">Da inviare</Link>.
          </div>
        )}
        {batch.status === "done" && (
          <div className="alert ok row-between">
            <span>
              Lotto inviato a Firefly.
              {rows.some((r) => r.status !== "imported") && " Le righe non inviate restano qui: riaprilo per inviarne altre."}
            </span>
            {rows.some((r) => r.status !== "imported") && (
              <button className="small" onClick={() => setStatus("reopen")}>Riapri</button>
            )}
          </div>
        )}
        {msg && <div className={`alert ${msg.kind}`}>{msg.text}</div>}
        <div className="row-between toolbar">
          <div className="tabs">
            {(
              [
                ["all", "Tutte"],
                ["todo", "Da importare"],
                ["nocat", "Senza categoria"],
                ["dup", "Duplicati"],
              ] as [Filter, string][]
            ).map(([f, l]) => (
              <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>
                {l}
              </button>
            ))}
          </div>
          <div className="bulk">
            <input list="dl-categories" placeholder="Categoria per le selezionate" value={bulkCat} disabled={approved} onChange={(e) => setBulkCat(e.target.value)} />
            <button disabled={!selectedRows.length || approved} onClick={bulkCategory}>Applica</button>
          </div>
        </div>
      </section>

      <section className="card tablewrap">
        <table className="grid rows">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  disabled={approved}
                  checked={visible.filter(selectable).length > 0 && visible.filter(selectable).every((r) => r.selected)}
                  onChange={(e) => setSelection(visible.filter(selectable).map((r) => r.id), e.target.checked)}
                />
              </th>
              <th>Data</th>
              <th>Descrizione / controparte</th>
              <th className="num">Importo</th>
              <th>Conto</th>
              <th>Categoria o giroconto</th>
              <th>Budget</th>
              <th>Tag</th>
              <th>Stato</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => {
              const locked = r.status === "imported" || approved;
              const out = parseFloat(r.amount) < 0;
              return (
                <tr key={r.id} className={`st-${r.status}`}>
                  <td>
                    <input type="checkbox" disabled={locked} checked={r.selected && r.status !== "imported"} onChange={() => setSelection([r.id], !r.selected)} />
                  </td>
                  <td className="nowrap">{fmtDate(r.date)}</td>
                  <td className="desc">
                    <EditText value={r.description} disabled={locked} onSave={(v) => patch(r, { description: v })} />
                    <EditText value={r.counterparty ?? ""} placeholder="controparte" disabled={locked} small onSave={(v) => patch(r, { counterparty: v || null })} />
                    {r.notes && r.notes !== r.description && <div className="notes" title={r.notes}>{r.notes}</div>}
                  </td>
                  <td className={`num nowrap ${out ? "neg" : "pos"}`}>{fmtAmount(r.amount)}</td>
                  <td>
                    {locked ? (
                      <AccountName id={r.account_id} />
                    ) : (
                      <AccountSelect value={r.account_id} onChange={(v) => patch(r, { account_id: v })} />
                    )}
                  </td>
                  <td>
                    {r.transfer_account_id ? (
                      <div className="transfer">
                        ↔{" "}
                        {locked ? (
                          <AccountName id={r.transfer_account_id} />
                        ) : (
                          <AccountSelect value={r.transfer_account_id} onChange={(v) => patch(r, { transfer_account_id: v })} />
                        )}
                      </div>
                    ) : (
                      <div className="catcell">
                        <EditText
                          value={r.category ?? ""}
                          list="dl-categories"
                          placeholder="categoria"
                          disabled={locked}
                          suggested={r.cat_source === "history" || r.cat_source === "bank"}
                          onSave={(v) => patch(r, { category: v || null })}
                        />
                        <CatSource row={r} />
                      </div>
                    )}
                    {!locked && !r.transfer_account_id && (
                      <details className="mini">
                        <summary>giroconto</summary>
                        <AccountSelect value={null} placeholder="verso/da conto…" onChange={(v) => v && patch(r, { transfer_account_id: v })} />
                      </details>
                    )}
                  </td>
                  <td>
                    {out && !r.transfer_account_id && (
                      <EditText value={r.budget ?? ""} list="dl-budgets" placeholder="budget" disabled={locked} onSave={(v) => patch(r, { budget: v || null })} />
                    )}
                  </td>
                  <td>
                    <EditText
                      value={(r.tags ?? []).join(", ")}
                      list="dl-tags"
                      placeholder="tag"
                      disabled={locked}
                      onSave={(v) => patch(r, { tags: v.split(",").map((t) => t.trim()).filter(Boolean) })}
                    />
                  </td>
                  <td>
                    <span className={`pill st-${r.status}`} title={r.dup_reason ?? r.error ?? ""}>
                      {STATUS_LABEL[r.status]}
                    </span>
                    {(r.dup_reason || r.error) && <div className="reason">{r.error ?? r.dup_reason}</div>}
                  </td>
                  <td>
                    {!locked && (
                      <button className="ghost small" title="Crea una regola da questa riga" onClick={() => setRuleFor(r)}>
                        + regola
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {preview && <PreviewModal preview={preview} busy={busy} onCancel={() => setPreview(null)} />}

      {ruleFor && (
        <Modal onClose={() => setRuleFor(null)}>
            <h3>Nuova regola</h3>
            <p className="muted small">
              Da: <b>{ruleFor.description}</b> {ruleFor.counterparty && <>· {ruleFor.counterparty}</>} · {fmtAmount(ruleFor.amount)}
            </p>
            <RuleForm
              initial={{
                ...emptyRule,
                name: suggestPattern(ruleFor),
                pattern: suggestPattern(ruleFor),
                field: ruleFor.counterparty ? "counterparty" : "any",
                direction: parseFloat(ruleFor.amount) < 0 ? "out" : "in",
                set_category: ruleFor.category,
                set_budget: ruleFor.budget,
                set_transfer_account_id: ruleFor.transfer_account_id,
                set_tags: ruleFor.tags?.length ? ruleFor.tags : null,
              }}
              onSaved={afterRuleSaved}
              onCancel={() => setRuleFor(null)}
            />
        </Modal>
      )}
      {meta && !meta.firefly_ok && <div className="alert err">Firefly non raggiungibile: {meta.error}</div>}
    </div>
  );
}

const SOURCE: Record<string, [string, string]> = {
  rule: ["R", "Da una regola"],
  history: ["S", "Suggerita dallo storico Firefly"],
  bank: ["B", "Categoria proposta dalla banca"],
  manual: ["M", "Impostata a mano"],
};

function CatSource({ row }: { row: Row }) {
  if (!row.category || !row.cat_source) return null;
  const [label, title] = SOURCE[row.cat_source];
  const why = row.cat_source === "history" ? row.raw?._suggerimento : row.cat_source === "rule" ? `regola #${row.rule_id}` : null;
  return (
    <span className={`pill src src-${row.cat_source}`} title={why ? `${title}: ${why}` : title}>
      {label}
    </span>
  );
}

function AccountName({ id }: { id: string | null }) {
  const { accountName } = useMeta();
  return <>{accountName(id)}</>;
}

function EditText({
  value,
  onSave,
  placeholder,
  list,
  disabled,
  small,
  suggested,
}: {
  value: string;
  onSave: (v: string) => void;
  placeholder?: string;
  list?: string;
  disabled?: boolean;
  small?: boolean;
  suggested?: boolean;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <input
      className={`cell ${small ? "small" : ""} ${suggested ? "suggested" : ""}`}
      value={v}
      list={list}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => v !== value && onSave(v.trim())}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}
