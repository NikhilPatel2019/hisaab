import { type DragEvent, useEffect, useState } from "react";

import { client, type Mapping, ok, type Preview, type S } from "../api/client";
import { Amount, Chip, ErrorBox, Field, Loading, PageHead } from "../components/ui";
import { money, shortDate } from "../format";
import { useAsync } from "../hooks";
import { Link, useLocation } from "../router";

const DATE_FORMATS = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%d-%b-%Y", "%d %b %y", "%d-%b-%y", "%d %B %Y", "%d-%B-%Y", "%b %d, %Y", "%m/%d/%Y"];

/** "%d/%m/%Y" → "31/12/2026", so people pick a format by example. */
function example(fmt: string): string {
  return fmt.replace("%d", "31").replace("%m", "12").replace("%Y", "2026").replace("%y", "26").replace("%B", "December").replace("%b", "Dec");
}

export function Import() {
  const { query } = useLocation();
  const accounts = useAsync(() => ok(client.GET("/api/accounts")), []);
  const history = useAsync(() => ok(client.GET("/api/imports")), []);
  const [accountId, setAccountId] = useState<number | null>(query.get("account") ? Number(query.get("account")) : null);
  const [file, setFile] = useState<{ name: string; content: string } | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [result, setResult] = useState<S["ImportResult"] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);

  const txnAccounts = (accounts.data ?? []).filter((a) => a.tracking === "transactions" && !a.archived);
  useEffect(() => {
    if (accountId === null && txnAccounts.length === 1) setAccountId(txnAccounts[0].id);
  }, [txnAccounts, accountId]);

  async function runPreview(m: Mapping | null, f = file, acct = accountId) {
    if (!f || acct === null) return;
    setBusy(true);
    setErr(null);
    try {
      const p = await ok(client.POST("/api/imports/preview", { body: { account_id: acct, filename: f.name, content: f.content, mapping: m } }));
      setPreview(p);
      setMapping(p.mapping);
    } catch (e) {
      setPreview(null);
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function pick(f: File | undefined) {
    if (!f) return;
    if (f.size > 5_000_000) return setErr("That file is over 5 MB. Export a shorter date range.");
    const content = await f.text();
    const next = { name: f.name, content };
    setFile(next);
    setResult(null);
    await runPreview(null, next);
  }

  async function runImport() {
    if (!file || !mapping || accountId === null) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await ok(client.POST("/api/imports", { body: { account_id: accountId, filename: file.name, content: file.content, mapping } }));
      setResult(r);
      setPreview(null);
      setFile(null);
      history.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function undo(id: number) {
    if (!window.confirm("Remove every transaction this import added?")) return;
    await ok(client.DELETE("/api/imports/{import_id}", { params: { path: { import_id: id } } }));
    history.reload();
  }

  function update(change: Partial<Mapping>) {
    if (!mapping) return;
    const next = { ...mapping, ...change };
    setMapping(next);
    void runPreview(next);
  }

  if (accounts.error) return <ErrorBox message={accounts.error} onRetry={accounts.reload} />;
  if (!accounts.data) return <Loading />;

  const cols = preview?.columns ?? [];
  const colSelect = (label: string, key: keyof Mapping, optional = false) => (
    <Field label={label}>
      {(id) => (
        <select
          id={id}
          className="field sm"
          value={(mapping?.[key] as number | null | undefined) ?? ""}
          onChange={(e) => update({ [key]: e.target.value === "" ? null : Number(e.target.value) } as Partial<Mapping>)}
        >
          {optional && <option value="">None</option>}
          {cols.map((c, i) => (
            <option key={i} value={i}>
              {c || `Column ${i + 1}`}
            </option>
          ))}
        </select>
      )}
    </Field>
  );

  return (
    <>
      <PageHead title="Import a statement" sub="CSV exports from your bank or card. The same file twice adds nothing, so re-importing is always safe." />

      <div className="grid" style={{ alignItems: "start" }}>
        <section className="card s7 stack" aria-label="New import">
          <Field label="Into which account?" hint={<>Not listed? <Link href="/accounts?new=1" className="lnk" style={{ fontSize: 13 }}>Add an account</Link> first.</>}>
            {(id) => (
              <select
                id={id}
                className="field"
                value={accountId ?? ""}
                onChange={(e) => {
                  const v = e.target.value ? Number(e.target.value) : null;
                  setAccountId(v);
                  if (file && v !== null) void runPreview(mapping, file, v);
                }}
              >
                <option value="">Choose an account</option>
                {txnAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                    {a.mask ? ` •••• ${a.mask}` : ""}
                  </option>
                ))}
              </select>
            )}
          </Field>

          <label
            className={`dropzone${over ? " over" : ""}`}
            onDragOver={(e: DragEvent) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(e: DragEvent) => {
              e.preventDefault();
              setOver(false);
              void pick(e.dataTransfer.files[0]);
            }}
            style={{ opacity: accountId === null ? 0.5 : 1, pointerEvents: accountId === null ? "none" : "auto" }}
          >
            <span className="d" style={{ fontSize: 20, fontWeight: 800 }}>
              {file ? file.name : "Drop a CSV here"}
            </span>
            <span className="sub">{file ? "Pick another file to replace it" : "or click to choose one · read on this machine only"}</span>
            <input type="file" accept=".csv,.txt,text/csv" className="sr-only" onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = ""; // so choosing the same file again still triggers a preview
                void pick(f);
              }} disabled={accountId === null} />
          </label>

          {err && <ErrorBox message={err} />}
          {busy && !preview && <Loading label="Reading the file…" />}

          {preview && mapping && (
            <>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <Chip tone="acc">{preview.rows_new} new</Chip>
                {preview.rows_duplicate > 0 && <Chip>{preview.rows_duplicate} already in hisaab</Chip>}
                {preview.rows_skipped > 0 && <Chip>{preview.rows_skipped} lines skipped</Chip>}
                {preview.first_date && preview.last_date && (
                  <Chip>
                    {shortDate(preview.first_date)} – {shortDate(preview.last_date)}
                  </Chip>
                )}
              </div>

              <details>
                <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 14 }}>Columns we picked (change if something looks off)</summary>
                <div className="grid" style={{ gap: 12, marginTop: 12 }}>
                  <div className="s6">{colSelect("Date", "date_col")}</div>
                  <div className="s6">
                    <Field label="Date format">
                      {(id) => (
                        <select id={id} className="field sm" value={mapping.date_format} onChange={(e) => update({ date_format: e.target.value })}>
                          {DATE_FORMATS.map((f) => (
                            <option key={f} value={f}>
                              {example(f)}
                            </option>
                          ))}
                        </select>
                      )}
                    </Field>
                  </div>
                  <div className="s6">
                    <Field label="Description">
                      {(id) => (
                        <select id={id} className="field sm" value={mapping.description_cols[0]} onChange={(e) => update({ description_cols: [Number(e.target.value)] })}>
                          {cols.map((c, i) => (
                            <option key={i} value={i}>
                              {c || `Column ${i + 1}`}
                            </option>
                          ))}
                        </select>
                      )}
                    </Field>
                  </div>
                  <div className="s6">
                    <Field label="Amounts are">
                      {(id) => (
                        <select id={id} className="field sm" value={mapping.amount_mode} onChange={(e) => update({ amount_mode: e.target.value as Mapping["amount_mode"] })}>
                          <option value="single">One column, + and −</option>
                          <option value="split">Separate debit and credit columns</option>
                          <option value="type">Amount plus a Dr/Cr column</option>
                        </select>
                      )}
                    </Field>
                  </div>
                  {mapping.amount_mode === "split" ? (
                    <>
                      <div className="s6">{colSelect("Money out (debit)", "debit_col")}</div>
                      <div className="s6">{colSelect("Money in (credit)", "credit_col")}</div>
                    </>
                  ) : (
                    <>
                      <div className="s6">{colSelect("Amount", "amount_col")}</div>
                      {mapping.amount_mode === "type" && <div className="s6">{colSelect("Dr / Cr", "type_col")}</div>}
                    </>
                  )}
                  <div className="s6">{colSelect("Running balance", "balance_col", true)}</div>
                  {mapping.amount_mode === "single" && (
                    <label className="s12" style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 14 }}>
                      <input type="checkbox" checked={!!mapping.invert} onChange={(e) => update({ invert: e.target.checked })} />
                      Spends are positive numbers in this file (common for cards)
                    </label>
                  )}
                </div>
              </details>

              {preview.errors.length > 0 && (
                <div className="sub" style={{ color: "var(--warn-ink)" }}>
                  {preview.errors.slice(0, 3).join(" · ")}
                </div>
              )}

              <table className="tbl">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Description</th>
                    <th className="num">Amount</th>
                    <th className="num hide-sm">Balance</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.sample.map((r) => (
                    <tr key={r.line} style={{ opacity: r.duplicate ? 0.5 : 1 }}>
                      <td className="n" style={{ whiteSpace: "nowrap" }}>
                        {shortDate(r.posted_on)}
                      </td>
                      <td style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {r.description} {r.duplicate && <Chip>already in</Chip>}
                      </td>
                      <td className="num">
                        <Amount paise={r.amount_paise} />
                      </td>
                      <td className="num hide-sm" style={{ color: "var(--ink3)" }}>
                        {r.balance_paise === null || r.balance_paise === undefined ? "" : money(r.balance_paise)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <span className="sub">First {preview.sample.length} rows. Money out should show without a +; money in with a +.</span>

              <button type="button" className="btnp" disabled={busy || preview.rows_new === 0} onClick={runImport} style={{ alignSelf: "flex-start" }}>
                {busy ? "Importing…" : preview.rows_new === 0 ? "Nothing new to import" : `Import ${preview.rows_new} transactions`}
              </button>
            </>
          )}

          {result && <ImportDone r={result} accountId={accountId} />}
        </section>

        <section className="card s5" aria-labelledby="hist">
          <h2 id="hist" className="d h2" style={{ marginBottom: 8 }}>
            Past imports
          </h2>
          {history.data?.length === 0 && <p className="sub">None yet.</p>}
          {history.data?.map((h) => (
            <div key={h.id} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 10, alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--sep)" }}>
              <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                <span style={{ fontSize: 14, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{h.filename}</span>
                <span className="sub">
                  {h.account} · {h.rows_added} added{h.rows_duplicate ? `, ${h.rows_duplicate} already there` : ""} · {shortDate(h.created_at.slice(0, 10))}
                </span>
              </span>
              {h.transactions_left > 0 ? (
                <button type="button" className="btns" style={{ minHeight: 36 }} onClick={() => undo(h.id)}>
                  Undo
                </button>
              ) : (
                <Chip>Nothing added</Chip>
              )}
            </div>
          ))}
        </section>
      </div>
    </>
  );
}

function ImportDone({ r, accountId }: { r: S["ImportResult"]; accountId: number | null }) {
  const check = r.balance_check;
  return (
    <div className="hero stack" role="status" style={{ gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <span className="d" style={{ fontSize: 30, fontWeight: 800 }}>
          {r.rows_added} added
        </span>
        {check && (
          <span className="stamp d" style={{ color: check.matches ? "var(--up)" : "var(--down-ink)", fontSize: 15 }}>
            {check.matches ? "ADDS UP" : "DOESN’T ADD UP"}
          </span>
        )}
      </div>
      <span className="body">
        {r.rows_duplicate > 0 && `${r.rows_duplicate} were already in hisaab. `}
        {r.same_file_as_import && "You’ve imported this exact file before. "}
        {r.opening_balance_set_paise !== null && r.opening_balance_set_paise !== undefined && `Opening balance set to ${money(r.opening_balance_set_paise)} from the file’s running balance. `}
        {check &&
          (check.matches
            ? `The file says ${money(check.file_paise)} on ${shortDate(check.on)}, and so do we.`
            : `The file says ${money(check.file_paise)} on ${shortDate(check.on)}; we get ${money(check.ledger_paise)}. Check the opening balance or a missing earlier statement.`)}
      </span>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {r.uncategorised > 0 && (
          <Link href={`/transactions?uncategorised=1${accountId ? `&account=${accountId}` : ""}`} className="btnp">
            Sort {r.uncategorised} transactions
          </Link>
        )}
        {accountId && (
          <Link href={`/accounts/${accountId}`} className="btns">
            Open the account
          </Link>
        )}
      </div>
    </div>
  );
}
