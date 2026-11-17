import { useState } from "react";

import { client, type Dashboard, type DocStats, ok } from "../api/client";
import { BarChart, LineChart } from "../components/Charts";
import { Amount, Chip, Delta, Empty, ErrorBox, Loading, PageHead } from "../components/ui";
import { compact, longDate, money, monthLabel, shortDate } from "../format";
import { useAsync } from "../hooks";
import { Link, navigate } from "../router";

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

export function Home() {
  const [range, setRange] = useState<6 | 12>(12);
  const dash = useAsync(() => ok(client.GET("/api/dashboard", { params: { query: { months: 12 } } })), []);
  const docs = useAsync(() => ok(client.GET("/api/documents/stats")), []);

  if (dash.error) return <ErrorBox message={dash.error} onRetry={dash.reload} />;
  if (!dash.data) return <Loading />;
  const d = dash.data;
  if (!d.has_data) return <Welcome onLoaded={dash.reload} />;

  const series = d.series.slice(-range);
  const flows = d.cashflow.slice(-6);
  const owed = d.groups.filter((g) => g.total_paise < 0);

  return (
    <>
      <PageHead
        cap={longDate(d.as_of)}
        title={greeting()}
        actions={
          <>
            <Link href="/transactions?new=1" className="btns">
              Add a transaction
            </Link>
            <Link href="/import" className="btnp">
              Import a statement
            </Link>
          </>
        }
      />

      <div className="grid">
        <section className="hero s8" aria-labelledby="nw" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span id="nw" className="cap">
                Net worth
              </span>
              <span className="d n" style={{ fontSize: 56, fontWeight: 800, letterSpacing: -2, lineHeight: 1 }}>
                {money(d.net_worth_paise, { exact: false })}
              </span>
              <Delta paise={d.change_this_month_paise} suffix="this month" />
            </div>
            <div className="seg" role="group" aria-label="Range">
              {([6, 12] as const).map((r) => (
                <button key={r} type="button" aria-pressed={range === r} onClick={() => setRange(r)}>
                  {r === 6 ? "6M" : "1Y"}
                </button>
              ))}
            </div>
          </div>
          <LineChart
            labels={series.map((p) => monthLabel(p.on.slice(0, 7)))}
            tipTitle={(i) => (i === series.length - 1 ? `Today, ${shortDate(series[i].on)}` : shortDate(series[i].on))}
            series={[{ label: "Net worth", color: "var(--acc)", area: true, values: series.map((p) => p.net_worth_paise) }]}
            format={(v) => money(v, { exact: false })}
            axisFormat={compact}
            ariaLabel={`Net worth over the last ${series.length} months, now ${money(d.net_worth_paise)}`}
          />
        </section>

        <NeedsYou d={d} docs={docs.data} />

        <div className="s12" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 20 }}>
          {d.groups.map((g) => (
            <Link key={g.key} href="/accounts" className="card row-link" style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-start", borderBottom: "1px solid var(--line)" }}>
              <span className="cap">{g.total_paise < 0 ? `${g.label} owed` : g.label}</span>
              <span className="d n" style={{ fontSize: 28, fontWeight: 800 }}>
                {money(Math.abs(g.total_paise), { exact: false })}
              </span>
              <span className="sub">
                {g.accounts} account{g.accounts === 1 ? "" : "s"}
              </span>
            </Link>
          ))}
        </div>

        <section className="card s7" aria-labelledby="pace" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 id="pace" className="d h2">
              {monthLabel(d.as_of.slice(0, 7), true)} so far
            </h2>
            <Link href="/transactions" className="lnk">
              See transactions
            </Link>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <span className="d n" style={{ fontSize: 34, fontWeight: 800 }}>
              {money(d.month_spent_paise, { exact: false })}
            </span>
            <span className="sub" style={{ fontSize: 14 }}>
              spent · {money(d.last_month_same_day_paise, { exact: false })} by this day last month
            </span>
          </div>
          <LineChart
            height={170}
            labels={d.pace.map((p) => String(p.day))}
            tipTitle={(i) => `Day ${d.pace[i].day}`}
            series={[
              { label: "This month", color: "var(--acc)", values: d.pace.map((p) => p.this_month_paise ?? null) },
              { label: "Last month", color: "var(--ink4)", dashed: true, values: d.pace.map((p) => p.last_month_paise ?? null) },
            ]}
            format={(v) => money(v, { exact: false })}
            axisFormat={compact}
            ariaLabel="Spending so far this month compared with last month, day by day"
          />
        </section>

        <section className="card s5" aria-labelledby="where">
          <h2 id="where" className="d h2" style={{ marginBottom: 12 }}>
            Where it went this month
          </h2>
          {d.top_categories.length === 0 && <p className="sub">No spending yet this month.</p>}
          {d.top_categories.map((c) => {
            const max = d.top_categories[0].spent_paise || 1;
            const q = c.category_id === null || c.category_id === undefined ? "uncategorised=1" : `category=${c.category_id}`;
            return (
              <Link key={c.name} href={`/transactions?${q}`} className="row-link" style={{ gridTemplateColumns: "minmax(0,1fr) auto", padding: "10px 4px", borderBottom: "1px solid var(--sep)" }}>
                <span style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <span style={{ fontSize: 14, fontWeight: 500 }}>{c.name}</span>
                  <span style={{ height: 6, borderRadius: 999, background: "var(--well)" }}>
                    <span style={{ display: "block", height: 6, borderRadius: 999, width: `${(c.spent_paise / max) * 100}%`, background: c.category_id ? "var(--acc)" : "var(--warn)" }} />
                  </span>
                </span>
                <span className="n" style={{ fontWeight: 600 }}>
                  {money(c.spent_paise, { exact: false })}
                </span>
              </Link>
            );
          })}
        </section>

        <section className="card s8" aria-labelledby="flow" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <h2 id="flow" className="d h2">
            Money in and out
          </h2>
          <BarChart
            labels={flows.map((m) => monthLabel(m.month))}
            tipTitle={(i) => monthLabel(flows[i].month, true)}
            series={[
              { label: "Income", color: "var(--c1)", values: flows.map((m) => m.income_paise) },
              { label: "Spent", color: "var(--c2)", values: flows.map((m) => m.spent_paise) },
              { label: "Invested", color: "var(--c3)", values: flows.map((m) => m.invested_paise) },
            ]}
            format={(v) => money(v, { exact: false })}
            axisFormat={compact}
            ariaLabel="Income, spending and investing for the last six months"
          />
          <span className="sub">Transfers between your own accounts and card bill payments aren’t counted twice.</span>
        </section>

        <section className="card s4" aria-labelledby="recent">
          <h2 id="recent" className="d h2" style={{ marginBottom: 8 }}>
            Latest
          </h2>
          {d.recent.map((t) => (
            <div key={t.id} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 12, alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--sep)" }}>
              <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                <span style={{ fontSize: 14, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.payee || t.description}</span>
                <span className="sub">
                  {shortDate(t.posted_on)} · {t.category ?? "Not sorted yet"}
                </span>
              </span>
              <Amount paise={t.amount_paise} />
            </div>
          ))}
        </section>

        {owed.length > 0 && <span className="sub s12">Card balances are what you owe now; they aren’t losses.</span>}
      </div>
    </>
  );
}

function NeedsYou({ d, docs }: { d: Dashboard; docs?: DocStats }) {
  const items: { href: string; title: string; sub: string; tone: "acc" | "warn" }[] = [];
  const waiting = docs ? docs.needs_password + docs.needs_review : 0;
  if (docs && waiting > 0)
    items.push({
      href: "/postbox?f=need",
      title: `${waiting} statement${waiting === 1 ? "" : "s"} in the postbox`,
      sub: docs.needs_password
        ? `${docs.needs_password} locked: add a password once and they open.`
        : `Tell us what ${waiting === 1 ? "it is" : "they are"}; we’ll remember.`,
      tone: "warn",
    });
  if (d.uncategorised > 0)
    items.push({
      href: "/transactions?uncategorised=1",
      title: `Sort ${d.uncategorised} transaction${d.uncategorised === 1 ? "" : "s"}`,
      sub: "Pick a category once; rules arrive in a later version.",
      tone: "acc",
    });
  for (const a of d.stale_accounts.slice(0, 3))
    items.push({
      href: `/accounts/${a.id}`,
      title: `Update ${a.name}`,
      sub: a.last_update ? `Last value from ${shortDate(a.last_update)}.` : "No value recorded yet.",
      tone: "warn",
    });
  return (
    <section className="card s4" aria-labelledby="ny" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <h2 id="ny" className="d h2">
          Needs you
        </h2>
        <Chip>{items.length}</Chip>
      </div>
      {items.length === 0 && <p className="body">All clear. Nothing waiting.</p>}
      {items.map((it) => (
        <Link key={it.href + it.title} href={it.href} className="row-link" style={{ gridTemplateColumns: "10px minmax(0,1fr)", padding: "12px 8px", margin: "0 -8px", borderRadius: 12, borderBottom: "none" }}>
          <span style={{ width: 10, height: 10, borderRadius: 999, background: it.tone === "warn" ? "var(--warn)" : "var(--acc)" }} />
          <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontSize: 15, fontWeight: 600 }}>{it.title}</span>
            <span className="sub">{it.sub}</span>
          </span>
        </Link>
      ))}
    </section>
  );
}

function Welcome({ onLoaded }: { onLoaded: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function loadDemo() {
    setBusy(true);
    try {
      await ok(client.POST("/api/demo"));
      onLoaded();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }
  return (
    <div className="stack">
      <PageHead cap="Welcome" title="Let’s set up your books" />
      <Empty
        title="Your money stays home."
        actions={
          <>
            <button type="button" className="btnp" onClick={() => navigate("/accounts?new=1")}>
              Add your first account
            </button>
            <Link href="/import" className="btns">
              Import a CSV
            </Link>
            <button type="button" className="btns" disabled={busy} onClick={loadDemo}>
              {busy ? "Loading…" : "Look around with demo data"}
            </button>
          </>
        }
      >
        Add the accounts you have — banks, cards, investments, EPF — then bring in transactions from a CSV export,
        or type them in. Demo data is a made-up household you can remove in one click from Settings.
      </Empty>
      {err && <ErrorBox message={err} />}
    </div>
  );
}
