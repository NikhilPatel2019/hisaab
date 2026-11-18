import { useEffect, useState } from "react";

import { client, type HoldingRow, ok, type Portfolio } from "../api/client";
import { Chip, Empty, ErrorBox, Loading, PageHead } from "../components/ui";
import { money, shortDate } from "../format";
import { useAsync } from "../hooks";
import { CLASS_COLORS, gainLabel, gainParts, holdingSource, pctLabel, unitsLabel } from "../invest";
import { Link } from "../router";

/** Poll the background runner until it's idle, then call `done`. */
function useRunner(done: () => void): [string | null, (label: string) => void] {
  const [running, setRunning] = useState<string | null>(null);
  useEffect(() => {
    if (running === null) return;
    const t = window.setInterval(async () => {
      try {
        const s = await ok(client.GET("/api/connectors/status"));
        if (!s.running) {
          setRunning(null);
          done();
        }
      } catch {
        setRunning(null);
      }
    }, 1500);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  return [running, setRunning];
}

export function Invest() {
  const p = useAsync(() => ok(client.GET("/api/investments")), []);
  const [msg, setMsg] = useState<{ text: string; tone: "acc" | "down" } | null>(null);
  const [running, setRunning] = useRunner(() => p.reload());

  async function start(kind: "prices" | "import", label: string) {
    setMsg(null);
    try {
      if (kind === "prices") await ok(client.POST("/api/investments/prices", { params: { query: { wait: false } } }));
      else await ok(client.POST("/api/investments/import", { params: { query: { wait: false, retry_failed: true } } }));
      setRunning(label);
    } catch (e) {
      setMsg({ text: e instanceof Error ? e.message : String(e), tone: "down" });
    }
  }

  if (p.error) return <ErrorBox message={p.error} onRetry={p.reload} />;
  if (!p.data) return <Loading />;
  const d = p.data;
  const busy = running !== null || d.running;

  const cap = [d.prices_as_of && `Prices ${shortDate(d.prices_as_of)}`, d.statement_date && `CAS ${shortDate(d.statement_date)}`]
    .filter(Boolean)
    .join(" · ");

  const actions = (
    <>
      <Link href="/postbox" className="btns">
        Upload a CAS
      </Link>
      {d.holdings.length > 0 && (
        <button type="button" className="btnp" disabled={busy} onClick={() => void start("prices", "Updating prices")}>
          {running === "Updating prices" ? "Updating…" : "Update prices"}
        </button>
      )}
    </>
  );

  return (
    <>
      <PageHead cap={cap || "From your CAS"} title="Investments" actions={actions} />
      {msg && (
        <Chip tone={msg.tone} style={{ alignSelf: "flex-start" }}>
          <span role="status">{msg.text}</span>
        </Chip>
      )}
      {running && (
        <Chip tone="acc" style={{ alignSelf: "flex-start" }}>
          <span role="status">{running}…</span>
        </Chip>
      )}
      {d.pending_imports > 0 && (
        <div className="card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span className="body">
            {d.pending_imports} CAS file{d.pending_imports === 1 ? "" : "s"} in the postbox {d.pending_imports === 1 ? "hasn’t" : "haven’t"} been read yet.
          </span>
          <button type="button" className="btns" disabled={busy} onClick={() => void start("import", "Reading CAS files")}>
            Read {d.pending_imports === 1 ? "it" : "them"} now
          </button>
        </div>
      )}
      <Problems problems={d.problems} />
      {d.last_prices && d.last_prices.errors.length > 0 && (
        <Chip tone="warn" style={{ alignSelf: "flex-start", whiteSpace: "normal" }}>
          Last price update: {d.last_prices.errors.join(" · ")}
        </Chip>
      )}

      {d.holdings.length === 0 ? (
        <Empty
          title="No investments yet"
          actions={
            <Link href="/postbox" className="btnp">
              Upload your CAS
            </Link>
          }
        >
          CDSL and NSDL email you a CAS every month with every share and fund you hold. Drop it in the Postbox (or
          connect your mailbox) and your holdings appear here. For your fund transactions and returns, ask CAMS or
          KFintech for a detailed CAS too.
        </Empty>
      ) : (
        <div className="grid">
          <Summary d={d} />
          <AccountsCard d={d} />
          <HoldingsTable holdings={d.holdings} total={d.total_paise} today={d.as_of} />
          <p className="sub s12" style={{ margin: 0 }}>
            On a statement’s date, units and values are exactly as printed. After that, funds are valued at AMFI’s NAV
            and shares at the exchange’s closing price. Bonds and NPS keep the statement’s value until the next one.
          </p>
        </div>
      )}
    </>
  );
}

function Problems({ problems }: { problems: Portfolio["problems"] }) {
  if (!problems.length) return null;
  return (
    <section className="card" aria-labelledby="cas-problems" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 id="cas-problems" className="d h2">
        Check these statements
      </h2>
      {problems.map((x) => (
        <div key={x.document_id} style={{ display: "flex", flexDirection: "column", gap: 4, paddingBottom: 8, borderBottom: "1px solid var(--sep)" }}>
          <span style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <Chip tone={x.status === "failed" ? "down" : "warn"}>{x.status === "failed" ? "Couldn’t read" : "Read with warnings"}</Chip>
            <Link href={`/postbox?doc=${x.document_id}`} className="lnk">
              {x.filename}
            </Link>
            {x.period_end && <span className="sub">to {shortDate(x.period_end)}</span>}
          </span>
          {x.message && <span className="sub">{x.message}</span>}
          {x.warnings.length > 0 && (
            <ul className="sub" style={{ margin: 0, paddingLeft: 18 }}>
              {x.warnings.slice(0, 5).map((w, i) => (
                <li key={i}>{w}</li>
              ))}
              {x.warnings.length > 5 && <li>and {x.warnings.length - 5} more</li>}
            </ul>
          )}
        </div>
      ))}
      <span className="sub">A warning means the parser’s numbers didn’t add up for that holding, so treat it with care.</span>
    </section>
  );
}

function Summary({ d }: { d: Portfolio }) {
  const gain = gainLabel(d.gain_paise, d.cost_paise);
  const xirr = pctLabel(d.xirr_pct);
  const total = d.total_paise || 1;
  return (
    <section className="hero s8" aria-labelledby="inv-total" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <span id="inv-total" className="cap">
        Worth now
      </span>
      <span className="d n" style={{ fontSize: 52, fontWeight: 800, letterSpacing: -1.5, lineHeight: 1 }}>
        {money(d.total_paise, { exact: false })}
      </span>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        {gain && (
          <Chip tone={(d.gain_paise ?? 0) >= 0 ? "up" : "down"} style={{ fontSize: 13, padding: "6px 12px" }}>
            {gain} on {money(d.cost_paise ?? 0, { exact: false })} put in
          </Chip>
        )}
        {xirr && (
          <Chip tone="acc" style={{ fontSize: 13, padding: "6px 12px" }}>
            {xirr} a year (XIRR)
          </Chip>
        )}
        {d.stale > 0 && <Chip tone="warn">{d.stale} price{d.stale === 1 ? "" : "s"} out of date</Chip>}
      </div>
      {d.cost_paise !== null && d.cost_paise !== undefined && d.cost_paise > 0 && (
        <span className="sub">
          Gain counts holdings whose cost your statements give
          {xirr ? "; XIRR uses the fund transactions from a detailed CAS" : ""}.
        </span>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <span className="cap">Your mix</span>
        <div className="mix-bar" role="img" aria-label={d.mix.map((m) => `${m.label} ${((m.value_paise / total) * 100).toFixed(1)}%`).join(", ")}>
          {d.mix.map((m) => (
            <span key={m.key} style={{ width: `${(m.value_paise / total) * 100}%`, background: CLASS_COLORS[m.key] }} />
          ))}
        </div>
        <ul className="mix-legend">
          {d.mix.map((m) => (
            <li key={m.key}>
              <span className="swatch" style={{ background: CLASS_COLORS[m.key] }} aria-hidden="true" />
              <span>{m.label}</span>
              <span className="n" style={{ fontWeight: 600 }}>
                {((m.value_paise / total) * 100).toFixed(1)}%
              </span>
              <span className="sub n">{money(m.value_paise, { exact: false })}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function AccountsCard({ d }: { d: Portfolio }) {
  return (
    <section className="card s4" aria-labelledby="inv-accounts" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <h2 id="inv-accounts" className="d h2" style={{ marginBottom: 8 }}>
        Where it’s held
      </h2>
      {d.accounts.map((a) => (
        <Link
          key={a.id}
          href={`/accounts/${a.id}`}
          className="row-link"
          style={{ gridTemplateColumns: "minmax(0,1fr) auto", padding: "12px 8px", margin: "0 -8px", borderRadius: 12 }}
        >
          <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
            <span style={{ fontSize: 15, fontWeight: 600 }}>{a.name}</span>
            <span className="sub">Statement {shortDate(a.statement_date)}</span>
          </span>
          <span className="n" style={{ fontWeight: 600 }}>
            {money(a.value_paise, { exact: false })}
          </span>
        </Link>
      ))}
    </section>
  );
}

type Sort = "value" | "gain";

export function HoldingsTable({ holdings, total, today, title = "Holdings" }: { holdings: HoldingRow[]; total: number; today: string; title?: string }) {
  const [sort, setSort] = useState<Sort>("value");
  const pct = (h: HoldingRow) => (h.gain_paise !== null && h.gain_paise !== undefined && h.cost_paise ? h.gain_paise / h.cost_paise : -Infinity);
  const rows = [...holdings].sort((a, b) => (sort === "value" ? b.value_paise - a.value_paise : pct(b) - pct(a)));
  const max = Math.max(...holdings.map((h) => h.value_paise), 1);
  return (
    <section className="card s12" aria-labelledby="inv-holdings" style={{ padding: "24px 28px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <h2 id="inv-holdings" className="d h2">
          {title} <span className="sub">{holdings.length}</span>
        </h2>
        <div className="seg" role="group" aria-label="Sort by">
          {(
            [
              ["value", "Worth now"],
              ["gain", "Gain %"],
            ] as const
          ).map(([id, label]) => (
            <button key={id} type="button" aria-pressed={sort === id} onClick={() => setSort(id)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      <table className="tbl" aria-label={title}>
        <thead>
          <tr>
            <th>Holding</th>
            <th className="num hide-sm">Units</th>
            <th className="num hide-sm">Price</th>
            <th className="num hide-sm">Put in</th>
            <th className="num">Worth now</th>
            <th className="num">Gain</th>
            <th className="hide-sm" style={{ width: 140 }}>
              Share
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((h) => {
            const g = gainParts(h.gain_paise, h.cost_paise);
            return (
              <tr key={`${h.account_id}-${h.instrument_id}-${h.folio ?? ""}`}>
                <td style={{ maxWidth: 360 }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    <span className="swatch" style={{ background: CLASS_COLORS[h.asset_class] }} aria-hidden="true" />
                    <span style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 500 }}>{h.name}</div>
                      <div className="sub" style={{ fontSize: 12 }}>
                        {holdingSource(h)}
                      </div>
                    </span>
                  </div>
                </td>
                <td className="num hide-sm">{unitsLabel(h.units)}</td>
                <td className="num hide-sm">
                  <div>₹{unitsLabel(h.price)}</div>
                  <div className="sub" style={{ fontSize: 12 }}>
                    {h.stale ? (
                      <Chip tone="warn" style={{ fontSize: 11, padding: "2px 8px" }}>
                        {shortDate(h.price_date, new Date(today + "T00:00:00"))} · old
                      </Chip>
                    ) : (
                      <>
                        {shortDate(h.price_date, new Date(today + "T00:00:00"))} · {h.price_source === "cas" ? "statement" : h.price_source.toUpperCase()}
                      </>
                    )}
                  </div>
                </td>
                <td className="num hide-sm">{h.cost_paise === null || h.cost_paise === undefined ? "—" : money(h.cost_paise, { exact: false })}</td>
                <td className="num" style={{ fontWeight: 600 }}>
                  {money(h.value_paise, { exact: false })}
                </td>
                <td className="num">
                  {g ? (
                    <>
                      <div style={{ color: (h.gain_paise ?? 0) >= 0 ? "var(--up)" : "var(--down-ink)", fontWeight: 600 }}>{g.amount}</div>
                      <div className="sub" style={{ fontSize: 12 }}>
                        {g.pct}
                        {h.xirr_pct !== null && h.xirr_pct !== undefined ? ` · XIRR ${pctLabel(h.xirr_pct)}` : ""}
                      </div>
                    </>
                  ) : (
                    <span className="sub">—</span>
                  )}
                </td>
                <td className="hide-sm">
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ flex: 1, height: 6, borderRadius: 999, background: "var(--well)" }}>
                      <span style={{ display: "block", height: 6, borderRadius: 999, width: `${(h.value_paise / max) * 100}%`, background: CLASS_COLORS[h.asset_class] }} />
                    </span>
                    <span className="sub n" style={{ fontSize: 12, width: 44, textAlign: "right" }}>
                      {((h.value_paise / (total || 1)) * 100).toFixed(1)}%
                    </span>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
