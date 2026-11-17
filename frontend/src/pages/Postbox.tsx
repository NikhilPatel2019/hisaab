import { type DragEvent, type FormEvent, useEffect, useRef, useState } from "react";

import { request } from "../api";
import { client, type Doc, type DocKind, ok, type UploadResult } from "../api/client";
import { Chip, Drawer, Empty, ErrorBox, Field, Loading, PageHead } from "../components/ui";
import { arrivedFrom, DOC_CODES, DOC_KINDS, docFrom, docTitle, receivedOn, senderDomain, sizeLabel, sourceLabel } from "../docs";
import { shortDate, todayISO } from "../format";
import { useAsync } from "../hooks";
import { Link, navigate, useLocation } from "../router";

type Filter = "all" | "need" | "banks" | "cards" | "investments";
const FILTERS: [Filter, string][] = [
  ["all", "All"],
  ["need", "Needs you"],
  ["banks", "Banks"],
  ["cards", "Cards"],
  ["investments", "Investments"],
];

function listQuery(f: Filter) {
  if (f === "need") return { status: "needs_you" as const, limit: 200 };
  if (f === "all") return { limit: 200 };
  return { group: f, limit: 200 };
}

const STATUS: Record<Doc["status"], { label: string; tone: "plain" | "warn" | "acc" }> = {
  filed: { label: "✓ Filed", tone: "plain" },
  needs_password: { label: "Password?", tone: "warn" },
  needs_review: { label: "What is it?", tone: "warn" },
  ignored: { label: "Ignored", tone: "plain" },
};

function href(f: Filter, doc?: number): string {
  const q = new URLSearchParams();
  if (f !== "all") q.set("f", f);
  if (doc) q.set("doc", String(doc));
  const s = q.toString();
  return s ? `/postbox?${s}` : "/postbox";
}

function checkedLabel(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  const time = d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const day = todayISO(d);
  return day === todayISO() ? `checked ${time}` : `checked ${shortDate(day)}, ${time}`;
}

export function Postbox() {
  const { query } = useLocation();
  const f = (FILTERS.some(([id]) => id === query.get("f")) ? query.get("f") : "all") as Filter;
  const openId = Number(query.get("doc")) || null;

  const stats = useAsync(() => ok(client.GET("/api/documents/stats")), []);
  const docs = useAsync(() => ok(client.GET("/api/documents", { params: { query: listQuery(f) } })), [f]);
  const needs = useAsync(() => ok(client.GET("/api/documents", { params: { query: { status: "needs_you", limit: 4 } } })), []);
  const conns = useAsync(() => ok(client.GET("/api/connectors")), []);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [note, setNote] = useState<{ text: string; tone: "acc" | "warn" | "down" } | null>(null);
  const [over, setOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const reloadAll = () => {
    stats.reload();
    docs.reload();
    needs.reload();
    conns.reload();
  };

  // While a sync runs, check on it every couple of seconds.
  useEffect(() => {
    if (syncing === null) return;
    const t = window.setInterval(async () => {
      try {
        const s = await ok(client.GET("/api/connectors/status"));
        if (s.running) setSyncing(s.current ?? "");
        else {
          setSyncing(null);
          reloadAll();
        }
      } catch {
        setSyncing(null);
      }
    }, 2000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [syncing === null]);

  async function syncNow() {
    setNote(null);
    try {
      const s = await ok(client.POST("/api/connectors/run", { params: { query: { wait: false } } }));
      setSyncing(s.current ?? "");
    } catch (e) {
      setNote({ text: e instanceof Error ? e.message : String(e), tone: "down" });
    }
  }

  async function upload(files: FileList | File[] | null | undefined) {
    const list = Array.from(files ?? []);
    if (!list.length) return;
    const form = new FormData();
    for (const file of list) form.append("files", file, file.name);
    setNote({ text: `Uploading ${list.length} file${list.length === 1 ? "" : "s"}…`, tone: "acc" });
    try {
      const res = await request<UploadResult[]>("/api/documents", { method: "POST", body: form });
      const count = (o: UploadResult["outcome"][]) => res.filter((r) => o.includes(r.outcome)).length;
      const parts = [`${count(["new"])} new`];
      if (count(["seen", "duplicate"])) parts.push(`${count(["seen", "duplicate"])} already here`);
      if (count(["rejected"])) parts.push(`${count(["rejected"])} not a PDF`);
      const locked = res.filter((r) => r.status === "needs_password" && r.outcome === "new").length;
      if (locked) parts.push(`${locked} need${locked === 1 ? "s" : ""} a password`);
      setNote({ text: parts.join(" · "), tone: count(["rejected"]) || locked ? "warn" : "acc" });
      reloadAll();
    } catch (e) {
      setNote({ text: e instanceof Error ? e.message : String(e), tone: "down" });
    }
  }

  if (stats.error) return <ErrorBox message={stats.error} onRetry={stats.reload} />;
  if (!stats.data || !conns.data) return <Loading />;
  const s = stats.data;
  const enabled = conns.data.filter((c) => c.enabled);
  const lastRun = conns.data.map((c) => c.last_run_at).filter(Boolean).sort().at(-1);
  const kinds = [...new Set(enabled.map((c) => ({ imap: "Email", drive: "Drive", folder: "Folder" })[c.kind]))];
  const cap = enabled.length
    ? [kinds.join(" + "), syncing !== null ? `syncing${syncing ? ` ${syncing}` : ""}…` : checkedLabel(lastRun)].filter(Boolean).join(" · ")
    : "Upload files, or connect your mailbox in Settings";
  const needCount = s.needs_password + s.needs_review;

  const uploadButton = (
    <>
      <input
        ref={fileInput}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        className="sr-only"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          void upload(files);
        }}
      />
      <button type="button" className="btns" onClick={() => fileInput.current?.click()}>
        Upload files
      </button>
    </>
  );

  return (
    <div
      className="stack"
      style={{ gap: 20 }}
      onDragOver={(e: DragEvent) => {
        if (Array.from(e.dataTransfer.types).includes("Files")) {
          e.preventDefault();
          setOver(true);
        }
      }}
      onDragLeave={(e: DragEvent) => e.currentTarget === e.target && setOver(false)}
      onDrop={(e: DragEvent) => {
        e.preventDefault();
        setOver(false);
        void upload(e.dataTransfer.files);
      }}
    >
      <PageHead
        cap={cap}
        title="Postbox"
        sub="Statements land here by themselves. Every original is kept exactly as it arrived."
        actions={
          <>
            {uploadButton}
            {enabled.length > 0 && (
              <button type="button" className="btnp" disabled={syncing !== null} onClick={syncNow}>
                {syncing !== null ? "Syncing…" : "Sync now"}
              </button>
            )}
          </>
        }
      />
      {note && (
        <Chip tone={note.tone} style={{ alignSelf: "flex-start" }}>
          <span role="status">{note.text}</span>
        </Chip>
      )}
      {over && (
        <div className="dropzone over" aria-hidden="true">
          <span className="d" style={{ fontSize: 20, fontWeight: 700 }}>Drop PDFs to keep them</span>
        </div>
      )}

      {s.kept === 0 ? (
        <Empty
          title="Nothing in the postbox yet"
          actions={
            <>
              <button type="button" className="btnp" onClick={() => fileInput.current?.click()}>
                Upload statements
              </button>
              <Link href="/settings#connections" className="btns">
                Connect Gmail or Drive
              </Link>
            </>
          }
        >
          Drop bank and card statements, CAS files or tax forms here, or connect your mailbox so they arrive on
          their own. Locked PDFs are fine: save the facts banks use for passwords once, and we’ll open them.
        </Empty>
      ) : (
        <div className="grid">
          <Stat label="Kept" value={String(s.kept)} sub="Each file stored once" />
          <Stat label="Duplicates skipped" value={String(s.duplicates_skipped)} sub="Same file, sent twice" />
          <Stat
            label="Filed on their own"
            value={s.auto_filed_pct === null || s.auto_filed_pct === undefined ? "—" : `${s.auto_filed_pct}%`}
            sub="No help needed"
          />
          <Stat label="Needs you" value={String(needCount)} sub={needCount ? "Locked, or not sorted yet" : "Nothing waiting"} />

          <section className="card s8" aria-labelledby="docs" style={{ padding: "24px 28px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
              <h2 id="docs" className="d h2">
                Statements
              </h2>
              <div className="seg" role="group" aria-label="Filter">
                {FILTERS.map(([id, label]) => (
                  <button key={id} type="button" aria-pressed={f === id} onClick={() => navigate(href(id))}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {docs.error && <ErrorBox message={docs.error} onRetry={docs.reload} />}
            {!docs.data ? (
              <Loading />
            ) : docs.data.items.length === 0 ? (
              <p className="sub">{f === "need" ? "Nothing needs you." : "Nothing here yet."}</p>
            ) : (
              <table className="tbl" aria-label="Statements">
                <thead>
                  <tr>
                    <th style={{ width: 44 }}>
                      <span className="sr-only">Type</span>
                    </th>
                    <th>Statement</th>
                    <th className="hide-sm">From</th>
                    <th className="hide-sm" style={{ width: 90 }}>
                      Received
                    </th>
                    <th className="num" style={{ width: 120 }}>
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {docs.data.items.map((d) => (
                    <tr key={d.id} className="clickable" onClick={() => navigate(href(f, d.id))}>
                      <td>
                        <span className="doc-code" aria-hidden="true">
                          {DOC_CODES[d.kind]}
                        </span>
                      </td>
                      <td style={{ maxWidth: 360 }}>
                        <Link href={href(f, d.id)} className="doc-title" onClick={(e) => e.stopPropagation()}>
                          {docTitle(d)}
                        </Link>
                        <div className="doc-file">{d.filename}</div>
                      </td>
                      <td className="hide-sm" style={{ color: "var(--ink3)" }}>
                        {docFrom(d)}
                      </td>
                      <td className="hide-sm n" style={{ color: "var(--ink3)", whiteSpace: "nowrap" }}>
                        {receivedOn(d)}
                      </td>
                      <td className="num">
                        <Chip tone={STATUS[d.status].tone}>{STATUS[d.status].label}</Chip>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {docs.data && docs.data.total > docs.data.items.length && (
              <p className="sub" style={{ marginBottom: 0 }}>
                Showing the latest {docs.data.items.length} of {docs.data.total}.
              </p>
            )}
          </section>

          <div className="s4" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <NeedsYou items={needs.data?.items ?? []} total={needCount} onChanged={reloadAll} />
            <Pipeline />
          </div>
        </div>
      )}
      {openId && <DocDrawer id={openId} onClose={() => navigate(href(f))} onChanged={reloadAll} />}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="card s3 stat" style={{ padding: 20, display: "flex", flexDirection: "column", gap: 6 }}>
      <span className="cap">{label}</span>
      <span className="d n" style={{ fontSize: 30, fontWeight: 800 }}>
        {value}
      </span>
      <span className="sub">{sub}</span>
    </div>
  );
}

function NeedsYou({ items, total, onChanged }: { items: Doc[]; total: number; onChanged: () => void }) {
  async function ignore(d: Doc) {
    await ok(client.PATCH("/api/documents/{doc_id}", { params: { path: { doc_id: d.id } }, body: { status: "ignored" } }));
    onChanged();
  }
  return (
    <section aria-labelledby="ny" className="needs-card">
      <span id="ny" className="needs-head">
        <span className="dot" />
        {total ? `${total} NEED${total === 1 ? "S" : ""} YOU` : "ALL CLEAR"}
      </span>
      {items.length === 0 && <span className="body">All clear. The next sync picks up from here.</span>}
      {items.map((d) => (
        <div key={d.id} className="needs-item">
          {d.status === "needs_password" ? (
            <>
              <span style={{ fontSize: 15, fontWeight: 600 }}>
                {d.kind === "unknown" ? d.filename : docTitle(d)} needs a password
              </span>
              <span className="sub">Add it once. We keep it encrypted and try it on every locked file, now and later.</span>
              <Link href={`/postbox?doc=${d.id}`} className="btnp" style={{ alignSelf: "flex-start" }}>
                Add password
              </Link>
            </>
          ) : (
            <>
              <span style={{ fontSize: 15, fontWeight: 600 }}>{arrivedFrom(d)}</span>
              <span className="sub">
                {d.filename} doesn’t look like a statement we know. What is it?
              </span>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button type="button" className="btns" onClick={() => void ignore(d)}>
                  Not financial
                </button>
                <Link href={`/postbox?doc=${d.id}`} className="btns">
                  Sort it
                </Link>
              </div>
            </>
          )}
        </div>
      ))}
      {total > items.length && (
        <Link href="/postbox?f=need" className="lnk">
          and {total - items.length} more
        </Link>
      )}
    </section>
  );
}

const PIPE: [string, string][] = [
  ["Your mailbox or Drive", "Connections in Settings fetch statement attachments every time you sync."],
  ["Originals kept as-is", "Each file is stored once, read-only, named by its fingerprint."],
  ["Unlocked with your facts", "Name, birth date or PAN, saved encrypted. Only the recipe that worked is remembered."],
  ["Sorted and filed", "Bank, card, CAS, tax. Your own rules come first."],
];

function Pipeline() {
  return (
    <section className="card" aria-labelledby="how" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <h2 id="how" className="d" style={{ margin: "0 0 10px", fontSize: 18, fontWeight: 700 }}>
        How statements get here
      </h2>
      <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {PIPE.map(([t, d], i) => (
          <li key={t} style={{ display: "grid", gridTemplateColumns: "28px minmax(0,1fr)", gap: 12, padding: "8px 0" }}>
            <span className="step-n n" aria-hidden="true">
              {i + 1}
            </span>
            <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <span style={{ fontSize: 14, fontWeight: 600 }}>{t}</span>
              <span className="sub">{d}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function DocDrawer({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const doc = useAsync(() => ok(client.GET("/api/documents/{doc_id}", { params: { path: { doc_id: id } } })), [id]);
  const accounts = useAsync(() => ok(client.GET("/api/accounts")), []);
  const changed = () => {
    doc.reload();
    onChanged();
  };

  if (doc.error)
    return (
      <Drawer title="Document" onClose={onClose}>
        <ErrorBox message={doc.error} onRetry={doc.reload} />
      </Drawer>
    );
  if (!doc.data)
    return (
      <Drawer title="Document" onClose={onClose}>
        <Loading />
      </Drawer>
    );
  const d = doc.data;
  const locked = !d.unlocked;
  const sources = [...new Set(d.sightings.map((x) => sourceLabel(x.source)))];
  const base = `/api/documents/${d.id}`;

  return (
    <Drawer title={docTitle(d)} onClose={onClose}>
      <div className="stack">
        <Chip tone={STATUS[d.status].tone} style={{ alignSelf: "flex-start" }}>
          {STATUS[d.status].label}
        </Chip>
        <dl className="facts">
          <dt>File</dt>
          <dd>
            {d.filename} · {sizeLabel(d.size_bytes)}
            {d.page_count ? ` · ${d.page_count} page${d.page_count === 1 ? "" : "s"}` : ""}
          </dd>
          {d.sender && (
            <>
              <dt>From</dt>
              <dd>{d.sender}</dd>
            </>
          )}
          {d.subject && (
            <>
              <dt>Subject</dt>
              <dd>{d.subject}</dd>
            </>
          )}
          <dt>Received</dt>
          <dd>{receivedOn(d)}</dd>
          <dt>Arrived by</dt>
          <dd>
            {sources.join(", ")}
            {d.copies_seen > 1 ? ` · seen ${d.copies_seen} times, kept once` : ""}
          </dd>
          {d.encrypted && (
            <>
              <dt>Password</dt>
              <dd>{d.opened_with ? `Opened with ${d.opened_with}` : "Locked"}</dd>
            </>
          )}
        </dl>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {!locked && (
            <a className="btns" href={`${base}/unlocked`} target="_blank" rel="noopener">
              Open
            </a>
          )}
          <a className="btns" href={`${base}/original?download=true`} download>
            Download original
          </a>
        </div>
        {locked && <UnlockForm doc={d} onDone={changed} />}
        {/* Remount after unlocking or refiling so the form shows the new values. */}
        <FileForm key={`${d.kind}|${d.status}|${d.period_end}`} doc={d} accounts={accounts.data ?? []} onDone={changed} />
      </div>
    </Drawer>
  );
}

function UnlockForm({ doc, onDone }: { doc: Doc; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [label, setLabel] = useState(doc.institution ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; tone: "acc" | "down" } | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const r = await ok(
        client.POST("/api/documents/{doc_id}/unlock", {
          params: { path: { doc_id: doc.id } },
          body: { password, save_as: label.trim() || null },
        }),
      );
      setPassword("");
      if (r.opened) {
        setMsg({
          text: r.also_unlocked ? `Opened, along with ${r.also_unlocked} other locked file${r.also_unlocked === 1 ? "" : "s"}.` : "Opened.",
          tone: "acc",
        });
        onDone();
      } else setMsg({ text: "That password didn’t open it.", tone: "down" });
    } catch (err) {
      setMsg({ text: err instanceof Error ? err.message : String(err), tone: "down" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit} style={{ padding: 20, gap: 12 }}>
      <h3 className="d" style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>
        Unlock it
      </h3>
      <span className="sub">
        Most banks build the password from your name, birth date or PAN. Save those once in{" "}
        <Link href="/settings#passwords" className="lnk">
          Settings
        </Link>{" "}
        and new files open on their own. Or type this file’s password:
      </span>
      <Field label="Password">
        {(fid) => (
          <input id={fid} className="field" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
        )}
      </Field>
      <Field label="Remember it as" hint="Kept encrypted, never shown again. We try it on other locked files too.">
        {(fid) => <input id={fid} className="field" maxLength={60} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Card statements" />}
      </Field>
      {msg && (
        <Chip tone={msg.tone} style={{ alignSelf: "flex-start" }}>
          <span role="status">{msg.text}</span>
        </Chip>
      )}
      <button type="submit" className="btnp" disabled={busy || !password} style={{ alignSelf: "flex-start" }}>
        {busy ? "Trying…" : "Unlock"}
      </button>
    </form>
  );
}

function FileForm({ doc, accounts, onDone }: { doc: Doc; accounts: { id: number; name: string; archived: boolean }[]; onDone: () => void }) {
  const [kind, setKind] = useState<DocKind>(doc.kind);
  const [institution, setInstitution] = useState(doc.institution ?? "");
  const [accountId, setAccountId] = useState<string>(doc.account_id ? String(doc.account_id) : "");
  const [start, setStart] = useState(doc.period_start ?? "");
  const [end, setEnd] = useState(doc.period_end ?? "");
  const [rule, setRule] = useState(false);
  const [msg, setMsg] = useState<{ text: string; tone: "acc" | "down" } | null>(null);
  const domain = senderDomain(doc.sender);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      await ok(
        client.PATCH("/api/documents/{doc_id}", {
          params: { path: { doc_id: doc.id } },
          body: {
            kind,
            institution: institution.trim() || null,
            account_id: accountId ? Number(accountId) : null,
            period_start: start || null,
            period_end: end || null,
          },
        }),
      );
      let extra = "";
      if (rule && domain && kind !== "unknown") {
        const r = await ok(
          client.POST("/api/doc-rules", {
            body: {
              name: `From ${domain}`,
              match_sender: `@${domain}`,
              kind,
              institution: institution.trim() || null,
              account_id: accountId ? Number(accountId) : null,
            },
          }),
        );
        extra = r.reclassified ? ` ${r.reclassified} other file${r.reclassified === 1 ? "" : "s"} refiled.` : "";
      }
      setMsg({ text: `Saved.${extra}`, tone: "acc" });
      onDone();
    } catch (err) {
      setMsg({ text: err instanceof Error ? err.message : String(err), tone: "down" });
    }
  }

  async function setStatus(status: "ignored" | "needs_review") {
    try {
      await ok(client.PATCH("/api/documents/{doc_id}", { params: { path: { doc_id: doc.id } }, body: { status } }));
      onDone();
    } catch (err) {
      setMsg({ text: err instanceof Error ? err.message : String(err), tone: "down" });
    }
  }

  return (
    <form className="stack" onSubmit={submit} style={{ gap: 12 }}>
      <h3 className="d" style={{ margin: "8px 0 0", fontSize: 17, fontWeight: 700 }}>
        Filing
      </h3>
      {doc.filed_by && (
        <span className="sub">
          {doc.filed_by === "you" ? "Filed by you." : doc.filed_by === "rule" ? "Filed by one of your rules." : "Filed automatically."}
        </span>
      )}
      <Field label="What is it?">
        {(fid) => (
          <select id={fid} className="field" value={kind} onChange={(e) => setKind(e.target.value as DocKind)}>
            {Object.entries(DOC_KINDS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label="Bank or provider">
        {(fid) => <input id={fid} className="field" maxLength={100} value={institution} onChange={(e) => setInstitution(e.target.value)} />}
      </Field>
      <Field label="Account">
        {(fid) => (
          <select id={fid} className="field" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            <option value="">Not linked</option>
            {accounts
              .filter((a) => !a.archived || String(a.id) === accountId)
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        )}
      </Field>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <Field label="Period from">{(fid) => <input id={fid} className="field sm" type="date" value={start} onChange={(e) => setStart(e.target.value)} />}</Field>
        <Field label="to">{(fid) => <input id={fid} className="field sm" type="date" value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)} />}</Field>
      </div>
      {domain && kind !== "unknown" && doc.filed_by !== "rule" && (
        <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 14 }}>
          <input type="checkbox" checked={rule} onChange={(e) => setRule(e.target.checked)} />
          File everything from {domain} like this
        </label>
      )}
      {msg && (
        <Chip tone={msg.tone} style={{ alignSelf: "flex-start" }}>
          <span role="status">{msg.text}</span>
        </Chip>
      )}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button type="submit" className="btnp">
          Save
        </button>
        {doc.status === "ignored" ? (
          <button type="button" className="btns" onClick={() => void setStatus("needs_review")}>
            Bring it back
          </button>
        ) : (
          <button type="button" className="btns" onClick={() => void setStatus("ignored")}>
            Not financial, ignore it
          </button>
        )}
      </div>
    </form>
  );
}
