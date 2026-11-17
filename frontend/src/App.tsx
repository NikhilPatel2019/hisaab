import { type FormEvent, useCallback, useEffect, useState } from "react";

import { ApiError, api, type SessionState } from "./api";
import { AccountDetail } from "./pages/AccountDetail";
import { Accounts } from "./pages/Accounts";
import { Home } from "./pages/Home";
import { Import } from "./pages/Import";
import { Postbox } from "./pages/Postbox";
import { Settings } from "./pages/Settings";
import { Transactions } from "./pages/Transactions";
import { Link, match, useLocation } from "./router";
import { applyTheme, loadTheme, type Theme } from "./theme";

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

const NAV: [string, string][] = [
  ["Home", "/"],
  ["Accounts", "/accounts"],
  ["Transactions", "/transactions"],
  ["Postbox", "/postbox"],
  ["Import", "/import"],
];

function Shell({ toggle, onLogout }: { toggle: React.ReactNode; onLogout: () => void }) {
  const { path } = useLocation();
  const accountMatch = match("/accounts/:id", path);
  let page: React.ReactNode;
  if (path === "/") page = <Home />;
  else if (path === "/accounts") page = <Accounts />;
  else if (accountMatch && /^\d+$/.test(accountMatch.id)) page = <AccountDetail id={Number(accountMatch.id)} />;
  else if (path === "/transactions") page = <Transactions />;
  else if (path === "/import") page = <Import />;
  else if (path === "/postbox") page = <Postbox />;
  else if (path === "/settings") page = <Settings onLogout={onLogout} />;
  else
    page = (
      <div className="hero stack">
        <span className="d" style={{ fontSize: 28, fontWeight: 800 }}>Nothing here</span>
        <Link href="/" className="btnp" style={{ alignSelf: "flex-start" }}>Go home</Link>
      </div>
    );

  const current = NAV.slice()
    .reverse()
    .find(([, href]) => (href === "/" ? path === "/" : path.startsWith(href)))?.[1];

  return (
    <>
      <a href="#main" className="sr-only">Skip to content</a>
      <header className="topbar">
        <Link href="/" className="brand" aria-label="hisaab home">
          <span className="brand-mark d">h</span>
          <span className="d" style={{ fontSize: 20, fontWeight: 700 }}>hisaab</span>
        </Link>
        <nav className="nav" aria-label="Main">
          {NAV.map(([label, href]) => (
            <Link key={href} href={href} aria-current={current === href ? "page" : undefined}>
              {label}
            </Link>
          ))}
        </nav>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          {toggle}
          <Link href="/settings" className="icon-btn" aria-label="Settings" title="Settings" aria-current={path === "/settings" ? "page" : undefined}>
            <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </Link>
        </div>
      </header>
      <main id="main" className="page" key={path}>
        {page}
      </main>
      <nav className="nav-mobile" aria-label="Main (mobile)">
        {NAV.map(([label, href]) => (
          <Link key={href} href={href} aria-current={current === href ? "page" : undefined}>
            {label}
          </Link>
        ))}
      </nav>
    </>
  );
}
