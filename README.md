# hisaab

**A personal finance dashboard for India that runs on your own machine.**
Bank and card statements, mutual funds, shares, tax and goals in one place, and nobody but you ever sees them.

hisaab is a template, not a service. There is no hisaab cloud, no sign-up and no telemetry.
You clone it, run it on your laptop or your own server, and point it at a database you control.

> **Status: M0 — the skeleton.** Login, database setup, encrypted secrets, Docker and CI work.
> The ledger and dashboards arrive from M1 onwards (see [Roadmap](#roadmap)).

## What you get today

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

## Commands

| | |
|---|---|
| `hisaab setup` | Write `.env`: database, password, keys. Won't overwrite an existing one without `--force`. |
| `hisaab serve` | Wait for the database, run migrations, start the server. |
| `hisaab migrate` | Bring the schema up to date. |
| `hisaab check` | Show the configuration without revealing secrets. |
| `make test` / `make lint` | Run the test suites / linters and type checks. |

## Security notes

- Keep `.env` (or `data/.env`) private. `HISAAB_SECRET_KEY` decrypts your stored secrets; if you lose it,
  they can't be recovered. Re-running setup with `--force` creates a new key.
- Without HTTPS, only use hisaab on `127.0.0.1` or a network you trust.
- Found a problem? Please open a private security advisory on GitHub rather than a public issue.

## Roadmap

| | Milestone |
|---|---|
| **M0** | Skeleton, login, three-database CI ← *you are here* |
| M1 | Ledger, CSV import, first net-worth dashboard |
| M2 | Statement vault (originals kept untouched) |
| M3 | Holdings from your CDSL/NSDL CAS |
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
