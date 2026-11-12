# Portfolio Manager

Tool to manage stock portfolios on a Mac desktop or the web.

A personal investment tracker for a household with several owners and
accounts. It imports broker exports, keeps a SQLite history of holdings and
cash, works out what each fund owns, and produces PDF reports, email
summaries, a live-price heat map, and a password-protected web UI.

Everything runs on your own machine or server. Real account data never
belongs in this repository (see [Privacy](#privacy)).

## Features

- **Holdings and history.** Daily stock snapshots and weekly fund snapshots
  in SQLite, with charts of fund balance over time.
- **Trades and ledger.** Trades, cash events and per-fund ledgers, including
  options, with unrealised and realised profit and loss.
- **Broker imports.** Fidelity activity CSVs and monthly statement PDFs,
  Hargreaves Lansdown CSVs and reports, AusSuper transaction CSVs and annual
  statements, a generic trades CSV, and a fund-list CSV.
- **Reports.** PDF portfolio reports and trade reports, plus email delivery
  of holdings, trade reports and option strike alerts.
- **Web UI.** Login with bcrypt-hashed passwords, server-side sessions, and
  owner-scoped accounts (an admin sees every owner; other users see only
  their own).
- **Desktop GUI.** A Tk trade-entry and reporting window (`trade_gui.py`).

## Project layout

```
main.py                 Command-line entry point (reports, trades, snapshots)
trades_cli.py           Trade and import commands used by main.py and the web UI
trade_gui.py            Desktop GUI (Tk)
init_database.py        First-time database setup
config.py               Loads owner names from settings.json
ledger/                 Database access, money parsing, prices, trade ledger
importers/              Broker CSV and PDF importers, fund-list importer
reports/                PDF reports, heat map, fund balance charts, alerts
web/                    FastAPI web UI (app, auth, services, password tool)
sample_data/            Fake broker exports and statements, and a demo loader
```

## Quick start

Requires Python 3.12 or newer.

```bash
git clone https://github.com/a12green/portfolio-manager.git
cd portfolio-manager

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp settings.example.json settings.json   # set your owner names
cp .env.example .env                      # fill in your settings (see below)

python init_database.py                   # creates data/ and the database
python -m web.set_password admin          # set the admin password (required)
python -m web                             # start the web UI on http://localhost:8080
```

The web server prints a warning at startup until the admin password is set,
and nobody can sign in until it is.

### Try it with sample data

The repository ships with fake broker exports and statements, so you can
explore the app without any real data:

```bash
python sample_data/seed_demo_db.py        # loads the samples into data/demo.sqlite3
STORAGE_DB_PATH=data/demo.sqlite3 python -m web
```

See [sample_data/README.md](sample_data/README.md) for what each file is and
how to regenerate the PDFs.

## Configuration

### Owners: `settings.json`

Copy `settings.example.json` to `settings.json` and list exactly three owner
names. Every owner-specific setting in the app refers to them as owner A,
B and C. The example uses `Household A`, `Household B` and `Household C`.

```json
{
  "owners": ["Household A", "Household B", "Household C"]
}
```

`settings.json` is git-ignored. Set `SETTINGS_PATH` to use a file elsewhere.

### Environment: `.env`

Copy `.env.example` and fill it in. The important groups are:

| Group | Variables |
| --- | --- |
| Google Sheets (optional) | `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_SHEET_NAME`, `GOOGLE_FUND_LIST_SHEET_NAME` |
| Fund list rows | `OWNER_A_FUND_ROWS` (for example `2:9`), `OWNER_B_FUND_ROW`, `OWNER_C_FUND_ROW` |
| Storage | `STORAGE_DB_PATH` (default `data/portfolio_history.sqlite3`) |
| Live prices | `LIVE_QUOTES`, `LIVE_QUOTES_POLL_SEC`, `LIVE_QUOTES_CACHE_SEC`, `MARKET_HOLIDAYS_ENABLED` |
| Email | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_SUBJECT` |
| Option alerts | `OPTION_STRIKE_ALERT_ENABLED`, `OPTION_STRIKE_ALERT_TO`, `OPTION_STRIKE_THRESHOLD` |
| Web UI | `WEB_HOST` (default `127.0.0.1`), `WEB_PORT`, `WEB_SECURE_COOKIES`, `WEB_TRUST_PROXY`, `WEB_SESSION_HOURS`, `WEB_PUBLIC_URL`, `WEB_RESET_TOKEN_MINUTES` |
| Fidelity | `FIDELITY_OWNER_B_HOLDER`, `FIDELITY_OWNER_B_ACCOUNT`, `FIDELITY_LLC_ACCOUNT_IDS` |

Google Sheets is optional. Without it, the app works from its own database and
the broker imports.

For Fidelity, set the owner-B holder line and account number from your own
statements. The defaults are placeholders. `FIDELITY_LLC_ACCOUNT_IDS` lists
account numbers that appear in LLC export file names, comma-separated.

### Google service account (optional)

If you use the Google Sheets features, create a service account in Google
Cloud, share the spreadsheet with its email address, and save its JSON key as
the file named by `GOOGLE_SERVICE_ACCOUNT_FILE`. Never commit that file.

## Command-line usage

`main.py` is the command-line entry point. Common commands:

```bash
# Snapshots
python main.py --snapshot-stocks-daily
python main.py --snapshot-funds-weekly

# Reports and email
python main.py --pdf-report
python main.py --email-holdings
python main.py --trade-report --trade-fund "Fund Name"
python main.py --email-trade-report --trade-fund "Fund Name"

# Funds and trades
python main.py --fund-add "Fund Name" --fund-owner "Household A" --fund-cash 100000 --fund-currency USD
python main.py --fund-list
python main.py --trade-add --trade-fund "Fund Name" --trade-ticker NVDA --trade-side buy --trade-shares 10 --trade-price 120
python main.py --trade-list
python main.py --import-trades-csv path/to/trades.csv

# Desktop GUI
python main.py --trade-gui
```

Run `python main.py --help` for the full list.

### Importing broker exports

Imports are run from `main.py` or from the web UI. The sample files in
`sample_data/` show the expected layout for each broker. Use your own real
exports in the same format.

## Web UI and user accounts

- `python -m web` serves the web UI. `WEB_HOST` and `WEB_PORT` set where it
  listens.
- The first account is `admin`. It sees every owner. Set its password with
  `python -m web.set_password admin`.
- Add more users with `python -m web.set_password <name> --create --owner "Household B"`.
  An owner-scoped user sees only that owner's data.
- Passwords are hashed with bcrypt. Changing a password signs the account out
  everywhere else.
- Password-reset emails use `WEB_PUBLIC_URL` to build the reset link.
- Session cookies are `Secure` by default, so they are sent only over HTTPS. For plain-HTTP local use, set `WEB_SECURE_COOKIES=false` in `.env`. Some browsers, Safari among them, refuse `Secure` cookies on `http://localhost`.

## Deployment

- **Docker.** `docker compose up --build` builds the image and serves the web
  UI on port 8080. `docker-compose.yml` mounts `./data` and expects the
  service-account file at `./service-account.json`. Adjust the path if yours
  differs.
- **PythonAnywhere.** `pythonanywhere_wsgi.py` wraps the FastAPI app for WSGI
  hosting. Set the environment variables in the Web tab rather than in `.env`.

## Running tests and checks

The test suite runs offline. It uses a temporary database for each test and
stubs the GBP/USD and live price lookups, so it needs no Google, Yahoo or SMTP
access.

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest                                  # 86 tests
python -m pytest --cov=. --cov-report=term-missing   # with line coverage
```

Coverage is about 42% of lines. The main gaps are the market-movers and
fund-cash reconciliation code (both need live services), the HL report and
portfolio-balance PDF importers, and the chart builders. Security findings and
their status are in [docs/security-audit.md](docs/security-audit.md).

## Privacy

Brokerage exports, statements and the SQLite database contain personal
financial data. Keep them out of version control. The `.gitignore` already
excludes `.env`, `settings.json`, `data/*.sqlite3`, and CSV and PDF files
outside `sample_data/`. Check `git status` before every commit.

## License

No license has been chosen yet. Until one is added, all rights are reserved.
