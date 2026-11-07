import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, Batch, fmtAmount, fmtDate, Row, RowStatus } from "../api";
import { AccountSelect, useMeta } from "../meta";
import RuleForm, { emptyRule } from "./RuleForm";
import PreviewModal, { Preview } from "./PreviewModal";

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
  const { meta, reload: reloadMeta } = useMeta();
  const [batch, setBatch] = useState<Batch | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState<Filter>("all");
  const [ruleFor, setRuleFor] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [bulkCat, setBulkCat] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);

  const load = useCallback(
    async (resetSelection = false) => {
      const b = await api.get<Batch>(`/batches/${id}`);
      setBatch(b);
      if (resetSelection) setSelected(new Set(b.rows!.filter((r) => r.status === "new" || r.status === "error").map((r) => r.id)));
    },
    [id],
  );

  useEffect(() => {
    load(true).catch((e) => setMsg({ kind: "err", text: e.message }));
  }, [load]);

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

  const selectable = (r: Row) => r.status !== "imported";
  const selectedRows = rows.filter((r) => selected.has(r.id) && selectable(r));
  const total = selectedRows.reduce((s, r) => s + parseFloat(r.amount), 0);

  function toggle(rid: number) {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(rid)) n.delete(rid);
      else n.add(rid);
      return n;
    });
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
      setPreview(await api.post<Preview>(`/batches/${id}/preview`, { row_ids: selectedRows.map((r) => r.id) }));
    } catch (e) {
      setMsg({ kind: "err", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function doImport(rowIds: number[]) {
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.post<{ imported: number; duplicate: number; error: number }>(`/batches/${id}/import`, {
        row_ids: rowIds,
      });
      setPreview(null);
      setMsg({
        kind: res.error ? "err" : "ok",
        text: `Importate ${res.imported}` + (res.duplicate ? ` · rifiutate come duplicate ${res.duplicate}` : "") + (res.error ? ` · errori ${res.error}` : ""),
      });
      await load(true);
      reloadMeta(true);
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

  async function afterRuleSaved() {
    setRuleFor(null);
    const res = await api.post<{ changed: number }>(`/batches/${id}/reapply-rules`);
    setMsg({ kind: "ok", text: `Regola salvata · ${res.changed} righe aggiornate` });
    await load();
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
            <button className="primary" disabled={busy || !selectedRows.length} onClick={openPreview}>
              {busy ? "Attendi…" : `Anteprima e importa ${selectedRows.length}`}
              {selectedRows.length > 0 && <small> ({fmtAmount(total)})</small>}
            </button>
          </div>
        </div>
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
            <input list="dl-categories" placeholder="Categoria per le selezionate" value={bulkCat} onChange={(e) => setBulkCat(e.target.value)} />
            <button disabled={!selectedRows.length} onClick={bulkCategory}>Applica</button>
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
                  checked={visible.filter(selectable).length > 0 && visible.filter(selectable).every((r) => selected.has(r.id))}
                  onChange={(e) =>
                    setSelected((s) => {
                      const n = new Set(s);
                      visible.filter(selectable).forEach((r) => (e.target.checked ? n.add(r.id) : n.delete(r.id)));
                      return n;
                    })
                  }
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
              const locked = r.status === "imported";
              const out = parseFloat(r.amount) < 0;
              return (
                <tr key={r.id} className={`st-${r.status}`}>
                  <td>
                    <input type="checkbox" disabled={locked} checked={selected.has(r.id) && !locked} onChange={() => toggle(r.id)} />
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
                        ↔ <AccountSelect value={r.transfer_account_id} onChange={(v) => patch(r, { transfer_account_id: v })} />
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

      {preview && <PreviewModal preview={preview} busy={busy} onConfirm={doImport} onCancel={() => setPreview(null)} />}

      {ruleFor && (
        <div className="modal" onClick={() => setRuleFor(null)}>
          <div className="card" onClick={(e) => e.stopPropagation()}>
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
          </div>
        </div>
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
