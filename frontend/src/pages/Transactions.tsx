import { type FormEvent, useEffect, useState } from "react";

import { type Account, type Category, client, ok, type Txn } from "../api/client";
import { Amount, Chip, Drawer, ErrorBox, Field, Loading, PageHead } from "../components/ui";
import { money, parseRupees, shortDate, todayISO } from "../format";
import { useAsync } from "../hooks";
import { navigate, useLocation } from "../router";

const KIND_LABEL: Record<string, string> = {
  expense: "Spending",
  income: "Income",
  transfer: "Between your accounts",
  investment: "Investing",
};

export function useCategories() {
  return useAsync(() => ok(client.GET("/api/categories")), []);
}

export function CategorySelect({
  categories,
  value,
  onChange,
  id,
  small,
  label,
}: {
  categories: Category[];
  value: number | null | undefined;
  onChange: (id: number | null) => void;
  id?: string;
  small?: boolean;
  label?: string;
}) {
  return (
    <select
      id={id}
      aria-label={label}
      className={`field${small ? " sm" : ""}`}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
      style={{ color: value ? "var(--ink)" : "var(--warn-ink)" }}
    >
      <option value="">Not sorted yet</option>
      {Object.entries(KIND_LABEL).map(([kind, label]) => (
        <optgroup key={kind} label={label}>
          {categories
            .filter((c) => c.kind === kind)
            .map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
        </optgroup>
      ))}
    </select>
  );
}

const PAGE = 50;

export function TxnTable({ accountId, onChanged }: { accountId?: number; onChanged?: () => void }) {
  const { query } = useLocation();
  const [q, setQ] = useState(query.get("q") ?? "");
  const [debounced, setDebounced] = useState(q);
  const [account, setAccount] = useState<number | null>(accountId ?? (query.get("account") ? Number(query.get("account")) : null));
  const [category, setCategory] = useState<number | null>(query.get("category") ? Number(query.get("category")) : null);
  const [uncat, setUncat] = useState(query.get("uncategorised") === "1");
  const [limit, setLimit] = useState(PAGE);
  const [editing, setEditing] = useState<Txn | null>(null);
  const [adding, setAdding] = useState(query.get("new") === "1");

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  const cats = useCategories();
  const accounts = useAsync(() => ok(client.GET("/api/accounts")), []);
  const page = useAsync(
    () =>
      ok(
        client.GET("/api/transactions", {
          params: {
            query: {
              q: debounced || undefined,
              account_id: account ?? undefined,
              category_id: category ?? undefined,
              uncategorised: uncat || undefined,
              limit,
            },
          },
        }),
      ),
    [debounced, account, category, uncat, limit],
  );

  async function setTxnCategory(t: Txn, id: number | null) {
    await ok(client.PATCH("/api/transactions/{txn_id}", { params: { path: { txn_id: t.id } }, body: { category_id: id } }));
    page.reload();
    onChanged?.();
  }

  const txnAccounts = (accounts.data ?? []).filter((a) => a.tracking === "transactions");
  return (
    <section className="card" aria-label="Transactions" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <input
          className="field sm"
          type="search"
          aria-label="Search transactions"
          placeholder="Search description, payee or notes"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setLimit(PAGE);
          }}
          style={{ flex: "1 1 240px", width: "auto" }}
        />
        {accountId === undefined && (
          <select className="field sm" aria-label="Account" value={account ?? ""} onChange={(e) => setAccount(e.target.value ? Number(e.target.value) : null)} style={{ width: "auto", minWidth: 180 }}>
            <option value="">All accounts</option>
            {txnAccounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        )}
        <select
          className="field sm"
          aria-label="Category"
          value={uncat ? "none" : (category ?? "")}
          onChange={(e) => {
            const v = e.target.value;
            setUncat(v === "none");
            setCategory(v && v !== "none" ? Number(v) : null);
            setLimit(PAGE);
          }}
          style={{ width: "auto", minWidth: 180 }}
        >
          <option value="">All categories</option>
          <option value="none">Not sorted yet</option>
          {(cats.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <button type="button" className="btnp" onClick={() => setAdding(true)} style={{ minHeight: 38 }}>
          Add
        </button>
      </div>

      {page.error && <ErrorBox message={page.error} onRetry={page.reload} />}
      {!page.data ? (
        <Loading />
      ) : (
        <>
          <div className="sub" aria-live="polite">
            {page.data.total} transaction{page.data.total === 1 ? "" : "s"} · net {money(page.data.sum_paise, { sign: true })}
          </div>
          {page.data.total === 0 ? (
            <p className="body">Nothing here yet.</p>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th style={{ width: 90 }}>Date</th>
                    <th>Description</th>
                    {accountId === undefined && <th className="hide-sm">Account</th>}
                    <th style={{ width: 220 }}>Category</th>
                    <th className="num">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {page.data.items.map((t) => (
                    <tr key={t.id} className="clickable" onClick={(e) => (e.target as HTMLElement).tagName !== "SELECT" && setEditing(t)}>
                      <td className="n" style={{ color: "var(--ink3)", whiteSpace: "nowrap" }}>
                        {shortDate(t.posted_on)}
                      </td>
                      <td style={{ maxWidth: 380 }}>
                        <div style={{ fontWeight: 500 }}>{t.payee || t.description}</div>
                        {t.payee && (
                          <div className="sub" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 12 }}>
                            {t.description}
                          </div>
                        )}
                      </td>
                      {accountId === undefined && (
                        <td className="hide-sm" style={{ color: "var(--ink3)" }}>
                          {t.account}
                        </td>
                      )}
                      <td onClick={(e) => e.stopPropagation()}>
                        <CategorySelect small categories={cats.data ?? []} value={t.category_id} onChange={(id) => setTxnCategory(t, id)} label={`Category for ${t.payee || t.description}`} />
                      </td>
                      <td className="num">
                        <Amount paise={t.amount_paise} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {page.data.items.length < page.data.total && (
            <button type="button" className="btns" style={{ alignSelf: "center" }} onClick={() => setLimit((l) => Math.min(500, l + PAGE * 2))}>
              Show more
            </button>
          )}
        </>
      )}

      {(editing || adding) && (
        <TxnForm
          existing={editing ?? undefined}
          accounts={txnAccounts}
          categories={cats.data ?? []}
          defaultAccount={account ?? undefined}
          onClose={() => {
            setEditing(null);
            setAdding(false);
            if (query.get("new")) navigate(location.pathname);
          }}
          onSaved={() => {
            setEditing(null);
            setAdding(false);
            page.reload();
            onChanged?.();
          }}
        />
      )}
    </section>
  );
}

function TxnForm({
  existing,
  accounts,
  categories,
  defaultAccount,
  onClose,
  onSaved,
}: {
  existing?: Txn;
  accounts: Account[];
  categories: Category[];
  defaultAccount?: number;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [accountId, setAccountId] = useState<number | null>(existing?.account_id ?? defaultAccount ?? accounts[0]?.id ?? null);
  const [postedOn, setPostedOn] = useState(existing?.posted_on ?? todayISO());
  const [direction, setDirection] = useState<"out" | "in">(existing && existing.amount_paise > 0 ? "in" : "out");
  const [amount, setAmount] = useState(existing ? String(Math.abs(existing.amount_paise) / 100) : "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [payee, setPayee] = useState(existing?.payee ?? "");
  const [category, setCategory] = useState<number | null>(existing?.category_id ?? null);
  const [notes, setNotes] = useState(existing?.notes ?? "");
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const paise = parseRupees(amount);
    if (paise === null || paise === 0) return setErr("Type an amount, like 450 or 1,250.50.");
    const signed = direction === "out" ? -Math.abs(paise) : Math.abs(paise);
    try {
      if (existing) {
        await ok(
          client.PATCH("/api/transactions/{txn_id}", {
            params: { path: { txn_id: existing.id } },
            body: { posted_on: postedOn, amount_paise: signed, description, payee: payee || null, category_id: category, notes: notes || null },
          }),
        );
      } else {
        if (accountId === null) return setErr("Add an account first.");
        await ok(
          client.POST("/api/transactions", {
            body: { account_id: accountId, posted_on: postedOn, amount_paise: signed, description, payee: payee || null, category_id: category, notes: notes || null },
          }),
        );
      }
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function remove() {
    if (!existing || !window.confirm("Delete this transaction?")) return;
    await ok(client.DELETE("/api/transactions/{txn_id}", { params: { path: { txn_id: existing.id } } }));
    onSaved();
  }

  return (
    <Drawer title={existing ? "Transaction" : "Add a transaction"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        {!existing && (
          <Field label="Account">
            {(id) => (
              <select id={id} className="field" value={accountId ?? ""} onChange={(e) => setAccountId(Number(e.target.value))} required>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            )}
          </Field>
        )}
        {existing && <span className="sub">{existing.account} · from {existing.source === "csv" ? "a CSV import" : existing.source}</span>}
        <div className="seg" role="group" aria-label="Direction" style={{ alignSelf: "flex-start" }}>
          <button type="button" aria-pressed={direction === "out"} onClick={() => setDirection("out")}>
            Money out
          </button>
          <button type="button" aria-pressed={direction === "in"} onClick={() => setDirection("in")}>
            Money in
          </button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Field label="Amount (₹)">{(id) => <input id={id} className="field" inputMode="decimal" required value={amount} onChange={(e) => setAmount(e.target.value)} />}</Field>
          <Field label="Date">{(id) => <input id={id} className="field" type="date" required value={postedOn} onChange={(e) => setPostedOn(e.target.value)} />}</Field>
        </div>
        <Field label="Description">{(id) => <input id={id} className="field" required maxLength={500} value={description} onChange={(e) => setDescription(e.target.value)} />}</Field>
        <Field label="Payee (optional)">{(id) => <input id={id} className="field" maxLength={200} value={payee} onChange={(e) => setPayee(e.target.value)} />}</Field>
        <Field label="Category">{(id) => <CategorySelect id={id} categories={categories} value={category} onChange={setCategory} />}</Field>
        <Field label="Notes (optional)">{(id) => <textarea id={id} className="field" value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
        {err && (
          <Chip tone="down" style={{ alignSelf: "flex-start" }}>
            {err}
          </Chip>
        )}
        <div style={{ display: "flex", gap: 10, justifyContent: "space-between" }}>
          {existing ? (
            <button type="button" className="btns" onClick={remove}>
              Delete
            </button>
          ) : (
            <span />
          )}
          <button type="submit" className="btnp">
            {existing ? "Save" : "Add"}
          </button>
        </div>
      </form>
    </Drawer>
  );
}

export function Transactions() {
  return (
    <>
      <PageHead title="Transactions" sub="Everything from every transaction-tracked account. Click a row to edit it." />
      <TxnTable />
    </>
  );
}
