import { type FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError, api, type Me, type SessionState } from "./api";
import { applyTheme, loadTheme, type Theme } from "./theme";

const NAV = ["Home", "Spend", "Invest", "Tax", "Goals", "Ask", "Wrapped"];

const SUN =
  "M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z";
const MOON = "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z";

export function App() {
  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [session, setSession] = useState<SessionState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => applyTheme(theme), [theme]);

  const refresh = useCallback(() => {
    api
      .session()
      .then(setSession)
      .catch(() => setError("Can't reach the hisaab server. Is it running?"));
  }, []);
  useEffect(refresh, [refresh]);

  const toggle = (
    <button
      type="button"
      className="icon-btn"
      onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
      aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <path
          d={theme === "dark" ? SUN : MOON}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );

  if (error) return <Centered toggle={toggle}><p className="body">{error}</p></Centered>;
  if (!session) return <Centered toggle={toggle}><p className="sub">Loading…</p></Centered>;
  if (session.setup_required) return <SetupNeeded toggle={toggle} />;
  if (!session.authenticated) return <Login toggle={toggle} onDone={refresh} />;
  return <Shell toggle={toggle} onLogout={refresh} />;
}

function Brand() {
  return (
    <span className="brand">
      <span className="brand-mark d">h</span>
      <span className="d" style={{ fontSize: 20, fontWeight: 700 }}>
        hisaab
      </span>
    </span>
  );
}

function Centered({ toggle, children }: { toggle: React.ReactNode; children: React.ReactNode }) {
  return (
    <>
      <header className="topbar">
        <Brand />
        {toggle}
      </header>
      <main style={{ display: "grid", placeItems: "center", minHeight: "calc(100vh - 68px)", padding: 16 }}>
        <div style={{ width: "100%", maxWidth: 440 }}>{children}</div>
      </main>
    </>
  );
}

function Login({ toggle, onDone }: { toggle: React.ReactNode; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await api.login(password);
      onDone();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Something went wrong.");
      setBusy(false);
    }
  }

  return (
    <Centered toggle={toggle}>
      <form className="hero" onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <span className="cap">Welcome back</span>
        <h1 className="d" style={{ margin: 0, fontSize: 40, fontWeight: 800, letterSpacing: -1, lineHeight: 1.05 }}>
          Your money stays home.
        </h1>
        <label htmlFor="pw" style={{ fontSize: 14, fontWeight: 600, marginTop: 8 }}>
          Password
        </label>
        <input
          id="pw"
          className="field"
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={message ? true : undefined}
          aria-describedby={message ? "pw-error" : undefined}
        />
        {message && (
          <span id="pw-error" role="alert" className="chip" style={{ alignSelf: "flex-start", background: "var(--down-tint)", color: "var(--down-ink)" }}>
            {message}
          </span>
        )}
        <button className="btnp" type="submit" disabled={busy || !password}>
          {busy ? "Checking…" : "Log in"}
        </button>
      </form>
    </Centered>
  );
}

function SetupNeeded({ toggle }: { toggle: React.ReactNode }) {
  return (
    <Centered toggle={toggle}>
      <div className="hero" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <span className="cap">One step first</span>
        <h1 className="d" style={{ margin: 0, fontSize: 34, fontWeight: 800 }}>
          Set a password
        </h1>
        <p className="body" style={{ margin: 0 }}>
          hisaab doesn’t have a password yet. In the folder you cloned, run:
        </p>
        <code style={{ padding: "12px 14px", borderRadius: 12, background: "var(--well)", border: "1px solid var(--line)" }}>
          make setup
        </code>
        <p className="sub" style={{ margin: 0 }}>
          It asks where your data should live, sets your password and writes a private <code>.env</code>. Then
          restart the server and reload this page.
        </p>
      </div>
    </Centered>
  );
}

const NEXT = [
  { m: "M1", t: "Ledger and first dashboard", d: "Accounts, transactions, CSV import, net worth." },
  { m: "M2", t: "Statement vault", d: "Originals kept exactly as the bank sent them." },
  { m: "M3", t: "Holdings from your CAS", d: "Mutual funds and shares, priced daily." },
];

function Shell({ toggle, onLogout }: { toggle: React.ReactNode; onLogout: () => void }) {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    api.me().then(setMe).catch(onLogout);
  }, [onLogout]);

  return (
    <>
      <header className="topbar">
        <Brand />
        <nav className="nav" aria-label="Main">
          {NAV.map((n, i) => (
            <span key={n} aria-current={i === 0 ? "page" : undefined} title={i === 0 ? undefined : "Coming in a later milestone"}>
              {n}
            </span>
          ))}
        </nav>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          {toggle}
          <button type="button" className="btns" onClick={() => api.logout().finally(onLogout)}>
            Log out
          </button>
        </div>
      </header>
      <main className="page">
        <div>
          <span className="cap">hisaab {me?.version ?? ""}</span>
          <h1 className="d" style={{ margin: "6px 0 0", fontSize: 36, fontWeight: 800, letterSpacing: -0.8 }}>
            You’re in.
          </h1>
        </div>
        <div className="grid">
          <section className="hero s8" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <span className="cap">Your setup</span>
            <span className="d" style={{ fontSize: 44, fontWeight: 800, letterSpacing: -1.2 }}>
              Ready for your data.
            </span>
            <span className="body">
              Nothing has been imported yet. Everything you add stays in your own database.
            </span>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 6 }}>
              <span className="chip" style={{ background: "var(--acc-tint)", color: "var(--acc-ink)" }}>
                ✓ Database: {me?.database ?? "…"}
              </span>
              <span
                className="chip"
                style={
                  me?.secrets_enabled
                    ? { background: "var(--acc-tint)", color: "var(--acc-ink)" }
                    : { background: "var(--warn-tint)", color: "var(--warn-ink)" }
                }
              >
                {me?.secrets_enabled ? "✓ Secrets encrypted" : "Secret key missing"}
              </span>
            </div>
          </section>
          <section className="card s4">
            <h2 className="d" style={{ margin: "0 0 12px", fontSize: 18 }}>
              Coming next
            </h2>
            {NEXT.map((x) => (
              <div key={x.m} style={{ display: "grid", gridTemplateColumns: "40px 1fr", gap: 12, padding: "10px 0", borderBottom: "1px solid var(--sep)" }}>
                <span className="chip" style={{ background: "var(--raised)", color: "var(--ink3)", justifyContent: "center" }}>
                  {x.m}
                </span>
                <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  <span style={{ fontSize: 14, fontWeight: 600 }}>{x.t}</span>
                  <span className="sub">{x.d}</span>
                </span>
              </div>
            ))}
          </section>
        </div>
      </main>
    </>
  );
}
