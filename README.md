# hisaab

**A personal finance dashboard for India that runs on your own machine.**
Bank and card statements, mutual funds, shares, tax and goals in one place, and nobody but you ever sees them.

hisaab is a template, not a service. There is no hisaab cloud, no sign-up and no telemetry.
You clone it, run it on your laptop or your own server, and point it at a database you control.

> **Status: M3 — investments.** Accounts, transactions, CSV import, net worth and the statement postbox
> work, and your CAS now becomes holdings: every fund and share, valued daily, with your returns.
> Broker sync and the nightly scheduler arrive in M4 (see [Roadmap](#roadmap)).

## What you get today

**Your money, in one place**

- Accounts of every kind: banks, cards, deposits, investments, EPF/PPF/NPS, property, loans.
  Track each one by **transactions** or, for things like EPF and FDs, by **value** you update now and then.
- **CSV import** from Indian bank and card exports. It finds the header under the bank's preamble,
  reads day-first dates, `1,23,456.78`, `Dr`/`Cr`, separate debit/credit columns and newest-first files.
  - Importing the same file twice adds **nothing**; overlapping exports only add what's new.
  - If the file has a running balance, the first import sets your opening balance from it and every
    import checks the result **adds up** to the paisa.
  - Every import can be undone.
- **Home dashboard**: net worth over 6 or 12 months, totals by account group, this month's spending
  against the same day last month, where it went, and money in/out/invested for six months.
- Categories made for Indian life (rent, food delivery, SIPs, EPF, card bill payments…). Transfers
  between your own accounts and card bill payments are never counted as spending twice.
- **Backups** as one JSON file that restores into any supported database, and a **demo household**
  (`make demo`) to look around before adding your own data.

**Postbox: every statement, kept once**

- Statements arrive from **Gmail or any IMAP mailbox** (with an app password), a **Google Drive folder**
  (read-only service account) or a **folder** on your machine — or just drop PDFs on the page.
- Each original is stored **exactly as it arrived**, once, read-only and named by its SHA-256. The same
  file sent twice, or seen in both your mail and Drive, is kept once. Re-running a sync adds nothing.
- **Locked PDFs open on their own**: save your name, date of birth, PAN or mobile once (encrypted) and
  hisaab tries the combinations Indian banks use — `NAME0703`, `07031990`, PAN, card digits… It remembers
  which recipe worked for each sender, never the password, and unlocked copies only ever exist in memory.
- Bank and card statements, CDSL/NSDL/CAMS/KFintech CAS, contract notes, AIS/26AS/Form 16 and payslips are
  recognised and matched to your accounts by their last four digits. Anything else waits in
  **Needs you**; your own filing rules come first.

**Investments from your CAS**

- Every CDSL/NSDL CAS (shares, ETFs, bonds, funds held in demat or in folios, NPS) and every CAMS/KFintech CAS
  in the postbox is read with [casparser](https://github.com/codereverser/casparser) (pinned) into
  holdings, one account per demat account plus one for your fund folios.
- On a statement's date, units and values are **exactly as printed**. After that, funds are valued at
  AMFI's NAV and shares and ETFs at the NSE (or BSE) closing price, and every price shows its date: old
  ones are flagged, never passed off as today's.
- A detailed CAMS/KFintech CAS brings your fund transactions too, for **XIRR** per fund and overall,
  and month-end history for the net-worth chart.
- If casparser's numbers don't add up for a holding, the warning is shown, not swallowed.
- Account and folio numbers are never stored: accounts are linked by a one-way hash, and only the last
  digits are shown.

**Private by design**

- One command to set up: choose where your data lives, set a password, keys are generated for you.
- Works with **SQLite** (default, nothing to install), **PostgreSQL**, **Supabase** or **MySQL/MariaDB**,
  through a single `HISAAB_DATABASE_URL`. Every schema change is tested on all of them in CI.
- Password login (argon2), session cookie, rate-limited attempts, CSRF protection, strict security headers.
- Refuses to start on a network address without a password, so you can't expose your finances by accident.
- An encrypted store for things like statement PDF passwords and broker API keys. Values are never shown or logged.
- Dark and light themes. Fonts are bundled, so the app makes no requests to third parties.

## Quick start

### Option A — with Python and Node (best for tinkering)

Needs [uv](https://docs.astral.sh/uv/) and Node 22.

```bash
git clone https://github.com/NikhilPatel2019/hisaab.git
cd hisaab
make setup     # installs dependencies, asks a few questions, writes .env
make run       # builds the web app and serves everything on http://127.0.0.1:8000
```

Want to look around first? `make demo` loads a made-up household (every shop and number is invented).
Remove it later from Settings or with `uv run --project backend hisaab demo --remove`.

For development with live reload, use `make dev` and open http://localhost:5173.

### Option B — with Docker

```bash
git clone https://github.com/NikhilPatel2019/hisaab.git
cd hisaab
make docker-setup   # same wizard, inside the container; writes data/.env
make docker-up      # http://127.0.0.1:8000, reachable only from this computer
```

Everything the app stores, including `data/.env`, lives in `./data`. Back up that folder.

## Choosing a database

| You want | Use this URL | Notes |
|---|---|---|
| Simplest, single file | `sqlite:///./data/hisaab.db` | Default. Back up by copying the file. |
| Your own Postgres | `postgresql://user:pass@host:5432/hisaab` | `postgres://` works too. |
| Your Supabase project | `postgresql://postgres:pass@db.<project>.supabase.co:5432/postgres` | SSL is turned on automatically. |
| MySQL or MariaDB | `mysql://user:pass@host:3306/hisaab` | `utf8mb4` is set automatically. |

Special characters in passwords must be URL-encoded (`%` → `%25`, `@` → `%40`).
To run a bundled database with Docker, add `--profile postgres` or `--profile mysql` and set
`POSTGRES_PASSWORD` / `MYSQL_PASSWORD` in a `.env` next to `compose.yaml`.

## Running it on a server

1. Point a domain (for example `money.example.com`) at your server.
2. Put `HISAAB_DOMAIN=money.example.com` in a `.env` next to `compose.yaml`.
3. `make docker-setup`, then `docker compose --profile server up -d`.

Caddy fetches and renews the HTTPS certificate, and cookies are marked secure.
The app is only reachable through Caddy; port 8000 isn't published.

## Importing a CSV

1. Add the account (Accounts → Add an account). Leave the opening balance empty if your export has a
   running-balance column.
2. Import → pick the account → drop the CSV. Check the preview: money out should show without a `+`.
   If the columns or date format look wrong, open "Columns we picked" and change them.
3. Import. Re-import whenever you like; duplicates are skipped.

Card exports often list spends as positive numbers; hisaab flips them automatically for credit-card
accounts (and leaves rows marked `Cr` alone). Reading transactions out of statement PDFs comes in M5.

## Bringing in statements

Open **Settings → Connections** and add one:

- **Gmail / IMAP.** Turn on 2-Step Verification, create an app password (Google Account → Security →
  App passwords) and paste it with your address. hisaab only reads: it searches for PDF attachments
  server-side, never marks or moves mail, and by default only downloads from banks, card issuers,
  depositories and brokers it knows (add your own sender domains if needed).
- **Google Drive.** Create a service account, enable the Drive API, download its JSON key, and share your
  statements folder with the service account's email as a Viewer. If another tool replaced a locked PDF
  with an unlocked copy, hisaab keeps the file's first revision — the original.
- **Folder.** Any folder on the machine running hisaab.

Then **Settings → Statement passwords**: save the facts banks build passwords from. Press **Sync now**
on the Postbox, or sync every night from cron:

```cron
15 0 * * *  cd /path/to/hisaab && make ingest prices >> data/ingest.log 2>&1
```

A CAS that lands in the postbox is read into holdings straight away (set `HISAAB_AUTO_IMPORT_CAS=false`
to do it by hand from the Investments page or with `hisaab cas`). Prices come from public end-of-day
files: AMFI's NAV list for funds, the NSE/BSE bhavcopy for shares and ETFs, and, for filling in past
months, fund NAV history from [api.mfapi.in](https://www.mfapi.in/). Add `make prices` to the same cron
job to keep values fresh.

Originals live in `data/vault/` (set `HISAAB_VAULT_DIR` to move it). **Back this folder up** along with
your database; the JSON backup doesn't include it.

## Commands

| | |
|---|---|
| `hisaab setup` | Write `.env`: database, password, keys. Won't overwrite an existing one without `--force`. |
| `hisaab serve` | Wait for the database, run migrations, start the server. |
| `hisaab migrate` | Bring the schema up to date. |
| `hisaab check` | Show the configuration without revealing secrets. |
| `hisaab demo` | Load a fictional household into an empty ledger (`--remove` takes it out). |
| `hisaab cas` | Read every CAS in the postbox into holdings (`--retry` for ones that failed). |
| `hisaab prices` | Fetch NAVs and closing prices and revalue holdings (`--backfill` fills in month-end history). |
| `hisaab cas-compat ORIGINAL UNLOCKED` | Check casparser reads an unlocked copy exactly like the original. Prints a verdict, never your numbers. |
| `hisaab ingest` | Fetch new statements from every connection (`--connector NAME`, `--folder PATH` for a one-off, `--reprocess` to retry passwords and refile). |
| `hisaab export [file]` | Write a JSON backup of every account, transaction and balance. |
| `hisaab restore file` | Load a backup into this database (empty, or `--replace`). Works across SQLite, Postgres and MySQL. |
| `make test` / `make lint` | Run the test suites / linters and type checks. |

## Security notes

- Keep `.env` (or `data/.env`) private. `HISAAB_SECRET_KEY` decrypts your stored secrets; if you lose it,
  they can't be recovered. Re-running setup with `--force` creates a new key.
- Without HTTPS, only use hisaab on `127.0.0.1` or a network you trust.
- Found a problem? Please open a private security advisory on GitHub rather than a public issue.

## Roadmap

| | Milestone |
|---|---|
| M0 | Skeleton, login, three-database CI ✓ |
| M1 | Ledger, CSV import, first net-worth dashboard ✓ |
| M2 | Statement vault (originals kept untouched), mail and Drive ingestion ✓ |
| **M3** | Holdings from your CAS, daily valuation, XIRR ← *you are here* |
| M4 | Connectors and the nightly sync |
| M5 | Bank and card statement parsing, checked to the paisa |
| M6 | Categories and merchant rules |
| M7 | Spending, recurring payments and dues |
| M8 | Tax estimate and capital gains |
| M9 | ITR-2 assist |
| M10 | Goals and card optimiser |
| M11 | Ask your data (local model by default) |
| M12 | Monthly review and v1.0 |

## Project layout

```
backend/    FastAPI app, SQLAlchemy models, Alembic migrations, CLI, tests
frontend/   React + Vite web app
deploy/     Caddy config for HTTPS
compose.yaml, Dockerfile, Makefile
```

## License

MIT. Not financial or tax advice: hisaab shows your own numbers; decisions are yours.
