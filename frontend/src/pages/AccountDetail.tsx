import { type FormEvent, useState } from "react";

import { client, ok } from "../api/client";
import { LineChart } from "../components/Charts";
import { Chip, ErrorBox, Field, Loading, PageHead } from "../components/ui";
import { ACCOUNT_KINDS, compact, money, parseRupees, shortDate, todayISO } from "../format";
import { useAsync } from "../hooks";
import { Link, navigate } from "../router";
import { AccountForm } from "./Accounts";
import { TxnTable } from "./Transactions";

export function AccountDetail({ id }: { id: number }) {
  const account = useAsync(() => ok(client.GET("/api/accounts/{account_id}", { params: { path: { account_id: id } } })), [id]);
  const snaps = useAsync(() => ok(client.GET("/api/accounts/{account_id}/snapshots", { params: { path: { account_id: id } } })), [id]);
  const [editing, setEditing] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (account.error) return <ErrorBox message={account.error} onRetry={account.reload} />;
  if (!account.data) return <Loading />;
  const a = account.data;
  const reload = () => {
    account.reload();
    snaps.reload();
  };

  async function archive() {
    try {
      await ok(client.PATCH("/api/accounts/{account_id}", { params: { path: { account_id: a.id } }, body: { archived: !a.archived } }));
      account.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }
  async function remove() {
    const n = a.transactions;
    if (!window.confirm(n ? `Delete ${a.name} and its ${n} transactions? This can’t be undone.` : `Delete ${a.name}?`)) return;
    try {
      await ok(client.DELETE("/api/accounts/{account_id}", { params: { path: { account_id: a.id }, query: { force: true } } }));
      navigate("/accounts");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  const owed = a.is_liability && a.balance_paise < 0;
  return (
    <>
      <PageHead
        cap={
          <>
            <Link href="/accounts" className="lnk" style={{ fontSize: 12 }}>
              Accounts
            </Link>{" "}
            · {ACCOUNT_KINDS[a.kind]}
            {a.institution ? ` · ${a.institution}` : ""}
            {a.mask ? ` · •••• ${a.mask}` : ""}
          </>
        }
        title={a.name}
        actions={
          <>
            <button type="button" className="btns" onClick={() => setEditing(true)}>
              Edit
            </button>
            <button type="button" className="btns" onClick={archive}>
              {a.archived ? "Unarchive" : "Archive"}
            </button>
            <button type="button" className="btns" onClick={remove}>
              Delete
            </button>
            {a.tracking === "transactions" && (
              <Link href={`/import?account=${a.id}`} className="btnp">
                Import CSV
              </Link>
            )}
          </>
        }
      />
      {err && <ErrorBox message={err} />}

      <div className="grid">
        <section className="hero s8" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span className="cap">{owed ? "You owe" : a.tracking === "balance" ? "Worth now" : "Balance"}</span>
          <span className="d n" style={{ fontSize: 52, fontWeight: 800, letterSpacing: -1.5, lineHeight: 1 }}>
            {money(owed ? -a.balance_paise : a.balance_paise)}
          </span>
          <span className="sub">
            {a.tracking === "balance"
              ? "Tracked by value. Update it whenever you check — monthly is plenty."
              : `${a.transactions} transactions · opening balance ${money(a.opening_balance_paise)}`}
          </span>
          {a.tracking === "balance" && snaps.data && snaps.data.length > 1 && (
            <LineChart
              height={180}
              labels={[...snaps.data].reverse().map((s) => shortDate(s.as_of))}
              series={[{ label: a.name, color: "var(--acc)", area: true, values: [...snaps.data].reverse().map((s) => s.balance_paise) }]}
              format={(v) => money(v)}
              axisFormat={compact}
              ariaLabel={`${a.name} value over time`}
            />
          )}
        </section>
        <SnapshotPanel accountId={a.id} tracking={a.tracking} snaps={snaps.data ?? []} onSaved={reload} />
      </div>

      {a.tracking === "transactions" && <TxnTable accountId={a.id} onChanged={account.reload} />}
      {editing && (
        <AccountForm
          existing={a}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            reload();
          }}
        />
      )}
    </>
  );
}

function SnapshotPanel({
  accountId,
  tracking,
  snaps,
  onSaved,
}: {
  accountId: number;
  tracking: "transactions" | "balance";
  snaps: { id: number; as_of: string; balance_paise: number; ledger_paise?: number | null; note?: string | null }[];
  onSaved: () => void;
}) {
  const today = todayISO();
  const [asOf, setAsOf] = useState(today);
  const [value, setValue] = useState("");
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const paise = parseRupees(value);
    if (paise === null) {
      setErr("Type an amount, like 3,07,800.");
      return;
    }
    try {
      await ok(client.POST("/api/accounts/{account_id}/snapshots", { params: { path: { account_id: accountId } }, body: { as_of: asOf, balance_paise: paise } }));
      setValue("");
      setErr(null);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <section className="card s4" aria-labelledby="snap" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 id="snap" className="d h2">
        {tracking === "balance" ? "Update the value" : "Check against the bank"}
      </h2>
      <span className="sub">
        {tracking === "balance"
          ? "What it’s worth on a date, from the passbook or app."
          : "Type the balance your bank shows for a date; we’ll tell you if it adds up."}
      </span>
      <form onSubmit={submit} className="stack" style={{ gap: 10 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <Field label="On">{(id) => <input id={id} className="field sm" type="date" value={asOf} max={today} onChange={(e) => setAsOf(e.target.value)} />}</Field>
          <Field label="Balance (₹)">{(id) => <input id={id} className="field sm" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />}</Field>
        </div>
        {err && <Chip tone="down">{err}</Chip>}
        <button type="submit" className="btns" disabled={!value}>
          Save
        </button>
      </form>
      <div>
        {snaps.slice(0, 6).map((s) => {
          const diff = s.ledger_paise === null || s.ledger_paise === undefined ? null : s.balance_paise - s.ledger_paise;
          return (
            <div key={s.id} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "8px 0", borderBottom: "1px solid var(--sep)", fontSize: 14 }}>
              <span>{shortDate(s.as_of)}</span>
              <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span className="n">{money(s.balance_paise)}</span>
                {diff !== null && (diff === 0 ? <Chip tone="acc">✓ adds up</Chip> : <Chip tone="warn">off by {money(Math.abs(diff))}</Chip>)}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
