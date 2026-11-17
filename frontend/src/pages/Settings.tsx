import { useState } from "react";

import { api } from "../api";
import { client, ok } from "../api/client";
import { Chip, ErrorBox, PageHead } from "../components/ui";
import { useAsync } from "../hooks";

export function Settings({ onLogout }: { onLogout: () => void }) {
  const me = useAsync(() => api.me(), []);
  const accounts = useAsync(() => ok(client.GET("/api/accounts")), []);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const demoCount = (accounts.data ?? []).filter((a) => a.is_demo).length;
  const empty = accounts.data?.length === 0;

  async function demo(load: boolean) {
    setErr(null);
    try {
      if (load) {
        const r = await ok(client.POST("/api/demo"));
        setMsg(`Loaded ${r.accounts} demo accounts with ${r.transactions} transactions.`);
      } else {
        if (!window.confirm("Remove all demo accounts and their transactions?")) return;
        const r = await ok(client.DELETE("/api/demo"));
        setMsg(`Removed ${r.accounts_removed} demo accounts.`);
      }
      accounts.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
      <PageHead title="Settings" />
      {msg && (
        <Chip tone="acc" style={{ alignSelf: "flex-start" }}>
          {msg}
        </Chip>
      )}
      {err && <ErrorBox message={err} />}
      <div className="grid">
        <section className="card s6 stack" aria-labelledby="backup">
          <h2 id="backup" className="d h2">
            Backup
          </h2>
          <p className="body" style={{ margin: 0 }}>
            One JSON file with every account, transaction and balance. It restores into any supported database with{" "}
            <code>hisaab restore</code>. Stored secrets stay out of it.
          </p>
          <a className="btnp" href="/api/export" download style={{ alignSelf: "flex-start" }}>
            Download a backup
          </a>
        </section>

        <section className="card s6 stack" aria-labelledby="demo">
          <h2 id="demo" className="d h2">
            Demo data
          </h2>
          <p className="body" style={{ margin: 0 }}>
            A made-up household with 18 months of history. Every shop and number is invented.
          </p>
          {demoCount > 0 ? (
            <button type="button" className="btns" onClick={() => demo(false)} style={{ alignSelf: "flex-start" }}>
              Remove demo data ({demoCount} accounts)
            </button>
          ) : (
            <button type="button" className="btns" disabled={!empty} onClick={() => demo(true)} style={{ alignSelf: "flex-start" }}>
              {empty ? "Load demo data" : "Only loads into an empty ledger"}
            </button>
          )}
        </section>

        <section className="card s6 stack" aria-labelledby="about">
          <h2 id="about" className="d h2">
            This install
          </h2>
          <div className="body">
            Version {me.data?.version ?? "…"} · database {me.data?.database ?? "…"} ·{" "}
            {me.data?.secrets_enabled ? "secrets encrypted" : "secret key missing"}
          </div>
          <button type="button" className="btns" onClick={() => api.logout().finally(onLogout)} style={{ alignSelf: "flex-start" }}>
            Log out
          </button>
        </section>
      </div>
    </>
  );
}
