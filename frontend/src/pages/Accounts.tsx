import { type FormEvent, useState } from "react";

import { type Account, client, ok, type S } from "../api/client";
import { Chip, Drawer, Empty, ErrorBox, Field, Loading, PageHead } from "../components/ui";
import { ACCOUNT_KINDS, money, parseRupees, shortDate } from "../format";
import { useAsync } from "../hooks";
import { Link, navigate, useLocation } from "../router";

const GROUPS: [string, string[]][] = [
  ["Bank & cash", ["bank", "cash", "wallet"]],
  ["Deposits", ["deposit"]],
  ["Investments", ["investment"]],
  ["Retirement", ["retirement"]],
  ["Property & other", ["property", "other_asset"]],
  ["Credit cards", ["credit_card"]],
  ["Loans", ["loan", "other_liability"]],
];

export function Accounts() {
  const { query } = useLocation();
  const [showArchived, setShowArchived] = useState(false);
  const accounts = useAsync(() => ok(client.GET("/api/accounts")), []);
  const adding = query.get("new") === "1";

  if (accounts.error) return <ErrorBox message={accounts.error} onRetry={accounts.reload} />;
  if (!accounts.data) return <Loading />;
  const list = accounts.data.filter((a) => showArchived || !a.archived);
  const archivedCount = accounts.data.filter((a) => a.archived).length;

  return (
    <>
      <PageHead
        cap={`${accounts.data.length} account${accounts.data.length === 1 ? "" : "s"}`}
        title="Accounts"
        actions={
          <>
            {archivedCount > 0 && (
              <button type="button" className="btns" onClick={() => setShowArchived((v) => !v)}>
                {showArchived ? "Hide archived" : `Show archived (${archivedCount})`}
              </button>
            )}
            <button type="button" className="btnp" onClick={() => navigate("/accounts?new=1")}>
              Add an account
            </button>
          </>
        }
      />
      {accounts.data.length === 0 && (
        <Empty title="No accounts yet">
          Start with the bank account your salary lands in. You can add cards, investments and EPF next.
        </Empty>
      )}
      <div className="grid">
        {GROUPS.map(([label, kinds]) => {
          const members = list.filter((a) => kinds.includes(a.kind));
          if (!members.length) return null;
          const total = members.reduce((s, a) => s + a.balance_paise, 0);
          return (
            <section key={label} className="card s6" aria-label={label}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
                <h2 className="d h2">{label}</h2>
                <span className="n" style={{ fontWeight: 700 }}>
                  {total < 0 ? `${money(-total, { exact: false })} owed` : money(total, { exact: false })}
                </span>
              </div>
              {members.map((a) => (
                <AccountRow key={a.id} a={a} />
              ))}
            </section>
          );
        })}
      </div>
      {adding && (
        <AccountForm
          onClose={() => navigate("/accounts")}
          onSaved={(a) => {
            accounts.reload();
            navigate(`/accounts/${a.id}`);
          }}
        />
      )}
    </>
  );
}

function AccountRow({ a }: { a: Account }) {
  return (
    <Link href={`/accounts/${a.id}`} className="row-link" style={{ gridTemplateColumns: "minmax(0,1fr) auto", padding: "12px 8px", margin: "0 -8px", borderRadius: 12 }}>
      <span style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
        <span style={{ fontSize: 15, fontWeight: 600, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {a.name}
          {a.mask && <span className="sub">•••• {a.mask}</span>}
          {a.is_demo && <Chip>Demo</Chip>}
          {a.archived && <Chip>Archived</Chip>}
        </span>
        <span className="sub">
          {[a.institution, a.tracking === "balance" ? "value updated by hand" : a.tracking === "holdings" ? "from your CAS" : `${a.transactions} transactions`, a.last_activity ? `last ${shortDate(a.last_activity)}` : null]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </span>
      <span className="n" style={{ fontWeight: 600 }}>
        {a.is_liability && a.balance_paise < 0 ? `${money(-a.balance_paise)} owed` : money(a.balance_paise)}
      </span>
    </Link>
  );
}

export function AccountForm({
  onClose,
  onSaved,
  existing,
}: {
  onClose: () => void;
  onSaved: (a: Account) => void;
  existing?: Account;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [kind, setKind] = useState<S["AccountIn"]["kind"]>(existing?.kind ?? "bank");
  const [tracking, setTracking] = useState<S["AccountIn"]["tracking"]>(existing?.tracking ?? "transactions");
  const [institution, setInstitution] = useState(existing?.institution ?? "");
  const [mask, setMask] = useState(existing?.mask ?? "");
  const [opening, setOpening] = useState(existing ? String(existing.opening_balance_paise / 100) : "");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const balanceKinds = ["deposit", "investment", "retirement", "property", "other_asset", "loan", "other_liability"];

  async function submit(e: FormEvent) {
    e.preventDefault();
    const openingPaise = opening.trim() ? parseRupees(opening) : 0;
    if (openingPaise === null) {
      setErr("Opening balance should be a number, like 25000 or -1200.50.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const body = {
        name,
        institution: institution || null,
        mask: mask || null,
        opening_balance_paise: openingPaise,
      };
      const saved = existing
        ? await ok(client.PATCH("/api/accounts/{account_id}", { params: { path: { account_id: existing.id } }, body }))
        : await ok(client.POST("/api/accounts", { body: { ...body, kind, tracking } }));
      onSaved(saved);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <Drawer title={existing ? "Edit account" : "Add an account"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <Field label="Name">{(id) => <input id={id} className="field" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="Salary account" />}</Field>
        {!existing && (
          <>
            <Field label="Type">
              {(id) => (
                <select
                  id={id}
                  className="field"
                  value={kind}
                  onChange={(e) => {
                    const k = e.target.value as typeof kind;
                    setKind(k);
                    setTracking(balanceKinds.includes(k) ? "balance" : "transactions");
                  }}
                >
                  {Object.entries(ACCOUNT_KINDS).map(([k, label]) => (
                    <option key={k} value={k}>
                      {label}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field
              label="How you’ll track it"
              hint={tracking === "balance" ? "You’ll update its value now and then — good for EPF, FDs, a house." : "Every transaction, from CSV imports or typed in."}
            >
              {(id) => (
                <select id={id} className="field" value={tracking} onChange={(e) => setTracking(e.target.value as typeof tracking)}>
                  <option value="transactions">Transactions</option>
                  <option value="balance">Balance only</option>
                </select>
              )}
            </Field>
          </>
        )}
        <Field label="Bank or provider (optional)">{(id) => <input id={id} className="field" maxLength={100} value={institution} onChange={(e) => setInstitution(e.target.value)} />}</Field>
        <Field label="Last 4 digits (optional)" hint="Only the last few digits. Never the full number.">
          {(id) => <input id={id} className="field" maxLength={4} inputMode="numeric" pattern="[0-9]*" value={mask} onChange={(e) => setMask(e.target.value.replace(/\D/g, ""))} />}
        </Field>
        {(existing ? existing.tracking : tracking) === "transactions" && (
          <Field label="Opening balance (₹)" hint="Leave empty if you’ll import a CSV with a running balance — we’ll work it out.">
            {(id) => <input id={id} className="field" inputMode="decimal" value={opening} onChange={(e) => setOpening(e.target.value)} placeholder="0" />}
          </Field>
        )}
        {err && (
          <Chip tone="down" style={{ alignSelf: "flex-start" }}>
            {err}
          </Chip>
        )}
        <button className="btnp" type="submit" disabled={busy || !name.trim()}>
          {busy ? "Saving…" : existing ? "Save" : "Add account"}
        </button>
      </form>
    </Drawer>
  );
}
