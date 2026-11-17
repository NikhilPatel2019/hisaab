import { type FormEvent, useState } from "react";

import { client, type Connector, ok } from "../api/client";
import { Chip, Drawer, ErrorBox, Field, Loading } from "../components/ui";
import { shortDate, todayISO } from "../format";
import { useAsync } from "../hooks";

type Msg = { text: string; tone: "acc" | "down" | "warn" } | null;
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

const FACTS: { key: "name" | "dob" | "pan" | "mobile"; label: string; hint: string; type: string; placeholder?: string }[] = [
  { key: "name", label: "Name as on your bank account", hint: "Banks often use its first four letters.", type: "text", placeholder: "First name is enough" },
  { key: "dob", label: "Date of birth", hint: "Used as DDMM, DDMMYYYY and similar.", type: "date" },
  { key: "pan", label: "PAN", hint: "CAS files and many banks use it.", type: "text", placeholder: "ABCDE1234F" },
  { key: "mobile", label: "Mobile number", hint: "Some issuers use its last digits.", type: "tel" },
];

/** Facts banks build PDF passwords from, plus one-off passwords. Values are write-only. */
export function StatementPasswords() {
  const state = useAsync(() => ok(client.GET("/api/statement-passwords")), []);
  const [values, setValues] = useState<Record<string, string>>({});
  const [label, setLabel] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState<Msg>(null);

  function unlockedText(n: number | undefined) {
    return n ? ` Opened ${n} locked file${n === 1 ? "" : "s"}.` : "";
  }

  async function saveFacts(e: FormEvent) {
    e.preventDefault();
    const body = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim()));
    if (!Object.keys(body).length) return;
    try {
      const r = await ok(client.PUT("/api/statement-passwords/facts", { body }));
      setValues({});
      setMsg({ text: `Saved.${unlockedText(r.unlocked_now)}`, tone: "acc" });
      state.reload();
    } catch (err) {
      setMsg({ text: errText(err), tone: "down" });
    }
  }

  async function forget(key: (typeof FACTS)[number]["key"]) {
    try {
      const body: Partial<Record<(typeof FACTS)[number]["key"], null>> = { [key]: null };
      await ok(client.PUT("/api/statement-passwords/facts", { body }));
      state.reload();
    } catch (err) {
      setMsg({ text: errText(err), tone: "down" });
    }
  }

  async function addPassword(e: FormEvent) {
    e.preventDefault();
    try {
      const r = await ok(client.POST("/api/statement-passwords", { body: { label, password } }));
      setLabel("");
      setPassword("");
      setMsg({ text: `Password saved.${unlockedText(r.unlocked_now)}`, tone: "acc" });
      state.reload();
    } catch (err) {
      setMsg({ text: errText(err), tone: "down" });
    }
  }

  async function remove(name: string) {
    if (!window.confirm("Remove this saved password? Files it opened stay in the postbox, but can’t be viewed unlocked.")) return;
    try {
      await ok(client.DELETE("/api/statement-passwords/{name}", { params: { path: { name } } }));
      state.reload();
    } catch (err) {
      setMsg({ text: errText(err), tone: "down" });
    }
  }

  return (
    <section id="passwords" className="card s6 stack" aria-labelledby="pw-h" style={{ alignSelf: "start" }}>
      <h2 id="pw-h" className="d h2">
        Statement passwords
      </h2>
      <p className="body" style={{ margin: 0 }}>
        Save these once and locked statements open on their own. They’re encrypted with your secret key and never shown
        again; when a file opens, we remember which combination worked, not the password.
      </p>
      {state.error && <ErrorBox message={state.error} onRetry={state.reload} />}
      {!state.data && !state.error && <Loading />}
      {state.data && (
        <>
          {state.data.locked_documents > 0 && (
            <Chip tone="warn" style={{ alignSelf: "flex-start" }}>
              {state.data.locked_documents} file{state.data.locked_documents === 1 ? "" : "s"} waiting for a password
            </Chip>
          )}
          <form className="stack" style={{ gap: 12 }} onSubmit={saveFacts} autoComplete="off">
            {FACTS.map((f) => (
              <Field key={f.key} label={f.label} hint={f.hint}>
                {(id) => (
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <input
                      id={id}
                      className="field"
                      type={f.type}
                      autoComplete="off"
                      placeholder={state.data?.facts[f.key] ? "Saved — type to replace" : f.placeholder}
                      value={values[f.key] ?? ""}
                      onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                    />
                    {state.data?.facts[f.key] && (
                      <button type="button" className="btns" onClick={() => void forget(f.key)} aria-label={`Forget ${f.label}`}>
                        Forget
                      </button>
                    )}
                  </div>
                )}
              </Field>
            ))}
            <button type="submit" className="btnp" style={{ alignSelf: "flex-start" }} disabled={!Object.values(values).some((v) => v.trim())}>
              Save
            </button>
          </form>

          <h3 className="d" style={{ margin: "8px 0 0", fontSize: 17, fontWeight: 700 }}>
            Other passwords
          </h3>
          {state.data.saved.length === 0 && <span className="sub">None yet. Add one when a bank uses something unusual.</span>}
          {state.data.saved.map((p) => (
            <div key={p.name} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, padding: "6px 0", borderBottom: "1px solid var(--sep)" }}>
              <span style={{ fontSize: 14 }}>
                {p.label} <span className="sub">· added {shortDate(todayISO(new Date(p.created_at)))}</span>
              </span>
              <button type="button" className="btns" onClick={() => void remove(p.name)}>
                Remove
              </button>
            </div>
          ))}
          <form onSubmit={addPassword} style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, alignItems: "end" }} autoComplete="off">
            <Field label="Label">{(id) => <input id={id} className="field" maxLength={60} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Old bank" />}</Field>
            <Field label="Password">
              {(id) => <input id={id} className="field" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />}
            </Field>
            <button type="submit" className="btns" style={{ minHeight: 48 }} disabled={!label.trim() || !password}>
              Add
            </button>
          </form>
        </>
      )}
      {msg && (
        <Chip tone={msg.tone} style={{ alignSelf: "flex-start" }}>
          <span role="status">{msg.text}</span>
        </Chip>
      )}
    </section>
  );
}

const KIND_LABEL: Record<Connector["kind"], string> = { imap: "Email (IMAP)", drive: "Google Drive folder", folder: "Folder on this server" };

function describe(c: Connector): string {
  const cfg = c.config as Record<string, unknown>;
  if (c.kind === "imap") return `${cfg.user} · ${cfg.host} · last ${cfg.since_months} months`;
  if (c.kind === "drive") return `Folder ${cfg.folder_id}`;
  return String(cfg.path);
}

function when(iso: string | null | undefined): string {
  if (!iso) return "never";
  const d = new Date(iso);
  const time = d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${shortDate(todayISO(d))}, ${time}`;
}

/** Where statements come from: a mailbox, a Drive folder, or a folder on the server. */
export function Connections() {
  const list = useAsync(() => ok(client.GET("/api/connectors")), []);
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  async function act(fn: () => Promise<unknown>, done?: string) {
    setMsg(null);
    try {
      await fn();
      if (done) setMsg({ text: done, tone: "acc" });
      list.reload();
    } catch (err) {
      setMsg({ text: errText(err), tone: "down" });
    }
  }

  return (
    <section id="connections" className="card s6 stack" aria-labelledby="conn-h" style={{ alignSelf: "start" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 id="conn-h" className="d h2">
          Connections
        </h2>
        <button type="button" className="btns" onClick={() => setAdding(true)}>
          Add a connection
        </button>
      </div>
      <p className="body" style={{ margin: 0 }}>
        Where statements come from. Each sync only fetches what’s new, and keeps every original in the postbox. Run{" "}
        <code>hisaab ingest</code> from a nightly cron job to sync while you sleep.
      </p>
      {list.error && <ErrorBox message={list.error} onRetry={list.reload} />}
      {list.data?.length === 0 && <span className="sub">No connections yet.</span>}
      {list.data?.map((c) => {
        const cfg = c.config as Record<string, unknown>;
        return (
          <div key={c.id} className="conn">
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
              <span style={{ fontSize: 15, fontWeight: 600 }}>
                {c.name} {!c.enabled && <Chip>Paused</Chip>}
              </span>
              <span className="sub">{KIND_LABEL[c.kind]}</span>
            </div>
            <span className="sub" style={{ overflowWrap: "anywhere" }}>
              {describe(c)}
            </span>
            {c.kind === "drive" && typeof cfg.service_account_email === "string" && (
              <span className="sub" style={{ overflowWrap: "anywhere" }}>
                Share the folder with <code>{cfg.service_account_email}</code> (Viewer).
              </span>
            )}
            {c.kind !== "folder" && !c.has_secret && <Chip tone="warn">Password or key missing</Chip>}
            {c.last_error ? (
              <Chip tone="down" style={{ whiteSpace: "normal" }}>
                {c.last_error}
              </Chip>
            ) : (
              <span className="sub">
                Last sync {when(c.last_run_at)}
                {c.last_summary ? ` · ${c.last_summary}` : ""}
              </span>
            )}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                className="btns"
                onClick={() =>
                  void act(
                    () => ok(client.POST("/api/connectors/{conn_id}/run", { params: { path: { conn_id: c.id }, query: { wait: false } } })),
                    "Sync started. The postbox fills in as files arrive.",
                  )
                }
              >
                Sync now
              </button>
              <button
                type="button"
                className="btns"
                onClick={() => void act(() => ok(client.PATCH("/api/connectors/{conn_id}", { params: { path: { conn_id: c.id } }, body: { enabled: !c.enabled } })))}
              >
                {c.enabled ? "Pause" : "Resume"}
              </button>
              <button
                type="button"
                className="btns"
                onClick={() =>
                  window.confirm(`Remove ${c.name}? Files it brought in stay in the postbox.`) &&
                  void act(() => ok(client.DELETE("/api/connectors/{conn_id}", { params: { path: { conn_id: c.id } } })))
                }
              >
                Remove
              </button>
            </div>
          </div>
        );
      })}
      {msg && (
        <Chip tone={msg.tone} style={{ alignSelf: "flex-start", whiteSpace: "normal" }}>
          <span role="status">{msg.text}</span>
        </Chip>
      )}
      {adding && (
        <ConnectionForm
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            list.reload();
          }}
        />
      )}
    </section>
  );
}

function ConnectionForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [kind, setKind] = useState<Connector["kind"]>("imap");
  const [name, setName] = useState("Gmail");
  const [user, setUser] = useState("");
  const [host, setHost] = useState("imap.gmail.com");
  const [secret, setSecret] = useState("");
  const [months, setMonths] = useState("24");
  const [knownOnly, setKnownOnly] = useState(true);
  const [extra, setExtra] = useState("");
  const [folderId, setFolderId] = useState("");
  const [path, setPath] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function pickKind(k: Connector["kind"]) {
    setKind(k);
    setSecret("");
    setName({ imap: "Gmail", drive: "Drive statements", folder: "Statements folder" }[k]);
  }

  async function readKey(file: File | undefined) {
    if (file) setSecret(await file.text());
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const config =
      kind === "imap"
        ? { user, host, since_months: Number(months), known_senders_only: knownOnly, extra_senders: extra }
        : kind === "drive"
          ? { folder_id: folderId }
          : { path };
    try {
      await ok(client.POST("/api/connectors", { body: { kind, name, config, secret: kind === "folder" ? null : secret, enabled: true } }));
      onSaved();
    } catch (e2) {
      setErr(errText(e2));
      setBusy(false);
    }
  }

  return (
    <Drawer title="Add a connection" onClose={onClose}>
      <form className="stack" onSubmit={submit} autoComplete="off">
        <div className="seg" role="group" aria-label="Type" style={{ alignSelf: "flex-start" }}>
          {(["imap", "drive", "folder"] as const).map((k) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => pickKind(k)}>
              {{ imap: "Email", drive: "Google Drive", folder: "Server folder" }[k]}
            </button>
          ))}
        </div>
        <Field label="Name">{(id) => <input id={id} className="field" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />}</Field>

        {kind === "imap" && (
          <>
            <p className="sub" style={{ margin: 0 }}>
              For Gmail, use an <strong>app password</strong>, not your Google password: turn on 2-Step Verification, then
              create one under Google Account → Security → App passwords. We only read mail; nothing is marked or moved.
            </p>
            <Field label="Email address">{(id) => <input id={id} className="field" type="email" required value={user} onChange={(e) => setUser(e.target.value)} />}</Field>
            <Field label="App password">
              {(id) => <input id={id} className="field" type="password" autoComplete="new-password" required value={secret} onChange={(e) => setSecret(e.target.value)} />}
            </Field>
            <Field label="Mail server" hint="imap.gmail.com for Gmail; your provider’s IMAP server otherwise.">
              {(id) => <input id={id} className="field" required value={host} onChange={(e) => setHost(e.target.value)} />}
            </Field>
            <Field label="Look back">
              {(id) => (
                <select id={id} className="field" value={months} onChange={(e) => setMonths(e.target.value)}>
                  {["6", "12", "24", "36", "60"].map((m) => (
                    <option key={m} value={m}>
                      {m} months
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <label style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 14 }}>
              <input type="checkbox" checked={knownOnly} onChange={(e) => setKnownOnly(e.target.checked)} />
              Only mail from banks, card issuers, depositories and brokers we know
            </label>
            {knownOnly && (
              <Field label="Also these senders (optional)" hint="Domains, separated by commas: mybank.example, payroll.example">
                {(id) => <input id={id} className="field" value={extra} onChange={(e) => setExtra(e.target.value)} />}
              </Field>
            )}
          </>
        )}

        {kind === "drive" && (
          <>
            <p className="sub" style={{ margin: 0 }}>
              Create a service account in Google Cloud, enable the Drive API, and download its JSON key. Then share your
              statements folder with the service account’s email as a Viewer. If a file was replaced by an unlocked copy,
              we keep its first version — the original.
            </p>
            <Field label="Folder link or id">{(id) => <input id={id} className="field" required value={folderId} onChange={(e) => setFolderId(e.target.value)} />}</Field>
            <Field label="Service-account key (JSON file)" hint={secret ? "Key loaded. It’s stored encrypted and never shown again." : undefined}>
              {(id) => <input id={id} className="field" type="file" accept="application/json,.json" required onChange={(e) => void readKey(e.target.files?.[0])} style={{ paddingTop: 12 }} />}
            </Field>
          </>
        )}

        {kind === "folder" && (
          <>
            <p className="sub" style={{ margin: 0 }}>
              A folder on the machine running hisaab — handy if another tool already saves statements there. PDFs in
              subfolders are included.
            </p>
            <Field label="Path">{(id) => <input id={id} className="field" required value={path} onChange={(e) => setPath(e.target.value)} placeholder="/home/me/Statements" />}</Field>
          </>
        )}

        {err && (
          <Chip tone="down" style={{ alignSelf: "flex-start", whiteSpace: "normal" }}>
            {err}
          </Chip>
        )}
        <button type="submit" className="btnp" disabled={busy}>
          {busy ? "Saving…" : "Save connection"}
        </button>
      </form>
    </Drawer>
  );
}
