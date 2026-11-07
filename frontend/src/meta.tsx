import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api, Meta } from "./api";

type MetaCtx = { meta: Meta | null; reload: (refresh?: boolean) => Promise<void>; accountName: (id: string | null) => string };

const Ctx = createContext<MetaCtx>({ meta: null, reload: async () => {}, accountName: (id) => id ?? "" });

export function MetaProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const reload = useCallback(async (refresh = false) => {
    setMeta(await api.get<Meta>(`/meta${refresh ? "?refresh=true" : ""}`));
  }, []);
  useEffect(() => {
    reload().catch(() => {});
  }, [reload]);
  const accountName = useCallback(
    (id: string | null) => (id ? meta?.accounts.find((a) => a.id === String(id))?.name ?? `#${id}` : "—"),
    [meta],
  );
  return <Ctx.Provider value={{ meta, reload, accountName }}>{children}</Ctx.Provider>;
}

export const useMeta = () => useContext(Ctx);

export function AccountSelect({
  value,
  onChange,
  placeholder = "—",
  className,
}: {
  value: string | null | undefined;
  onChange: (v: string | null) => void;
  placeholder?: string;
  className?: string;
}) {
  const { meta } = useMeta();
  return (
    <select className={className} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{placeholder}</option>
      {meta?.accounts
        .filter((a) => a.active || a.id === value)
        .map((a) => (
          <option key={a.id} value={a.id}>
            {a.name}
          </option>
        ))}
    </select>
  );
}

export function MetaDatalists() {
  const { meta } = useMeta();
  return (
    <>
      <datalist id="dl-categories">{meta?.categories.map((c) => <option key={c} value={c} />)}</datalist>
      <datalist id="dl-budgets">{meta?.budgets.map((c) => <option key={c} value={c} />)}</datalist>
      <datalist id="dl-tags">{meta?.tags.map((c) => <option key={c} value={c} />)}</datalist>
    </>
  );
}
