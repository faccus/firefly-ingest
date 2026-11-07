export type Account = { id: string; name: string; iban: string | null; currency: string | null; active: boolean };
export type Meta = {
  accounts: Account[];
  categories: string[];
  budgets: string[];
  tags: string[];
  firefly_ok: boolean;
  error: string | null;
};

export type RowStatus = "new" | "duplicate" | "possible_duplicate" | "skipped" | "imported" | "error";

export type Row = {
  id: number;
  batch_id: number;
  idx: number;
  date: string;
  amount: string;
  description: string;
  counterparty: string | null;
  notes: string | null;
  account_id: string | null;
  category: string | null;
  budget: string | null;
  tags: string[] | null;
  transfer_account_id: string | null;
  hash: string;
  status: RowStatus;
  dup_reason: string | null;
  rule_id: number | null;
  cat_source: "rule" | "history" | "bank" | "manual" | null;
  firefly_id: string | null;
  error: string | null;
  raw: Record<string, string | null> | null;
};

export type Batch = {
  id: number;
  source: string;
  filename: string | null;
  profile_id: number | null;
  account_id: string | null;
  status: "review" | "done";
  created_at: string;
  counts: Partial<Record<RowStatus, number>>;
  rows?: Row[];
};

export type Columns = {
  date: string | null;
  amount: string | null;
  debit: string | null;
  credit: string | null;
  description: string[];
  counterparty: string | null;
  notes: string | null;
  account?: string | null;
  category?: string | null;
  status?: string | null;
};

export type ProfileOptions = {
  account_map?: Record<string, string | null>;
  status_skip?: string[];
  use_bank_category?: boolean;
};

export type Profile = {
  id: number;
  name: string;
  account_id: string | null;
  delimiter: string | null;
  encoding: string | null;
  date_format: string | null;
  decimal_sep: "," | "." | null;
  invert_sign: number | boolean;
  columns: Columns;
  options: ProfileOptions | null;
};

export type Rule = {
  id: number;
  name: string | null;
  priority: number;
  field: "any" | "description" | "counterparty" | "notes";
  match_type: "contains" | "equals" | "starts" | "regex";
  pattern: string;
  direction: "any" | "in" | "out";
  account_id: string | null;
  amount_min: number | null;
  amount_max: number | null;
  set_category: string | null;
  set_budget: string | null;
  set_tags: string[] | null;
  set_counterparty: string | null;
  set_description: string | null;
  set_transfer_account_id: string | null;
  set_skip: number | boolean;
  active: number | boolean;
  hits: number;
};

export type Alias = { id: number; kind: "account" | "category"; alias: string; value: string };

export type Inspect = {
  upload_id: string;
  filename: string;
  header_row: number;
  headers: string[];
  sample: string[][];
  guess: Columns;
  distinct: Record<string, string[]>;
  encoding: string | null;
  delimiter: string | null;
};

export type UploadResult =
  | { batch_id: number; profile: string; rows: number }
  | { needs_profile: true; upload_id: string; filename: string };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(`/api${path}`, init);
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : res.statusText;
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

export const api = {
  get: <T,>(p: string) => request<T>("GET", p),
  post: <T,>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T,>(p: string, b: unknown) => request<T>("PUT", p, b),
  patch: <T,>(p: string, b: unknown) => request<T>("PATCH", p, b),
  del: <T,>(p: string) => request<T>("DELETE", p),
};

export function fmtAmount(a: string | number): string {
  const n = typeof a === "string" ? parseFloat(a) : a;
  return n.toLocaleString("it-IT", { style: "currency", currency: "EUR", signDisplay: "always" });
}

export function fmtDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}
