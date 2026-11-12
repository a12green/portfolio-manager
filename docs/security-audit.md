# Security audit

**Status as of this update (2026-11-12).** Two items are still open: the
Docker image has not been built or inspected, and the Yahoo Finance paths have
not been exercised against the live service. Everything else below is fixed,
closed, or accepted with a stated condition. The Google Sheets integration has
been removed, so the app no longer handles a service-account key.

**Scope:** the whole repository: the FastAPI web UI and its authentication, the
browser front end (`web/static/app.js`), the importers, the reports and email
code, the desktop GUI's dependency handling, the Dockerfile and
`.dockerignore`, and the dependency pins.

## Method

- Manual code review of the web layer, authentication and session handling,
  the front end's use of `innerHTML`, SQL construction, and file and secret
  handling.
- `bandit` static analysis, at medium severity and above, plus the lower
  severity patterns reviewed by hand.
- `pip-audit` against the pinned requirements.
- A test suite of 296 tests, run offline. Each fix below has a regression test.

**Not done:** a live penetration test, a browser-based XSS test, a
`docker build`, and running the Yahoo Finance integration against the live
service. The test suite replaces it with a fake.

## Findings fixed

| Severity | Finding | Fix | Commit |
| --- | --- | --- | --- |
| High | **Stored cross-site scripting in the web UI.** Fund names, tickers, notes, owner names and market-mover names went into `innerHTML` unescaped. A value saved by one owner could run script in the admin's session. The original file had 73 unescaped interpolations. | Every interpolated value in markup goes through `esc()`. `tests/test_frontend_escaping.py` fails if one is missed. It fails on the original file and passes now. | `bf8478b` |
| High | **Sign-in lockout bypass.** The rate limiter trusted `X-Forwarded-For` from any caller, so rotating that header gave each attempt a fresh bucket. | The header is used only when `WEB_TRUST_PROXY=true`, which should be set only behind a reverse proxy you run. | `52d42cf` |
| High | **Secrets and data could be baked into the Docker image.** There was no `.dockerignore`, so `COPY . .` included `.env`, `settings.json`, databases and statements. | `.dockerignore` excludes them. Not yet confirmed by a build; see Pending. | `1733849` |
| High | **Known-vulnerable dependency.** `pillow` 12.3.0 had 40 advisories, fixed in 13.0.0. | Pinned `pillow==13.0.0` and `ttkbootstrap==2.2.7`. The newer ttkbootstrap accepts pillow 13. `pip-audit` reports no known vulnerabilities. | `1733849`, `b1c196a` |
| Medium | **No security headers.** There was no Content-Security-Policy, framing protection or `nosniff`. | Every response sets CSP, `X-Frame-Options: DENY`, `nosniff`, a referrer policy and a permissions policy. HSTS is sent when secure cookies are on. | `38d0a59` |
| Medium | **Container ran as root.** | The image runs as an unprivileged `app` user. Only `/data` and `/app/reports` are writable. | `1733849` |
| Medium | **Server bound to all interfaces by default** (bandit B104). | Default `WEB_HOST` is `127.0.0.1` in the web entry point and in `main.py --serve-web`. Docker sets `0.0.0.0` itself. | `1733849`, `2a7654c` |
| Medium | **Unpinned dependencies**, and an unused `slowapi` dependency. | All direct dependencies are pinned. `slowapi` is removed. | `1733849` |
| Low | **Usernames could be found by timing.** An unknown username skipped the bcrypt check, so it answered faster. | An unknown username runs the same bcrypt check. | `52d42cf` |
| Low | **API responses could be cached by browsers.** | Every `/api/` response is `Cache-Control: no-store`. | `38d0a59` |
| Low | **Real account numbers and the account holder's name were in the source.** | Moved to `.env` (`FIDELITY_OWNER_B_HOLDER`, `FIDELITY_OWNER_B_ACCOUNT`, `FIDELITY_LLC_ACCOUNT_IDS`). A scan of all tracked files finds none. | `cb6e536` and earlier |
| Medium | **Internal error text was sent to the browser.** Ten API endpoints returned `str(exception)` on any unexpected error, which can include file paths and database messages. | Those endpoints now log the full error on the server and return `Internal error`. A regression test checks that a path in the error message does not reach the response. | `this update` |
| Low | **Expected refusals were turned into 500 errors.** An `HTTPException` raised inside a broad `try` block became a 500, so scope refusals and "not found" answers were reported as server faults. | Refusals pass through unchanged. Covered by the balance-update tests. | `this update` |
| Low | **The fund-creation service did not check owner scope itself.** The route checked it, but the service accepted the user without enforcing anything. | The service now enforces owner scope too, so any future caller is covered. | `this update` |

## Bugs found during testing

Two stopped the app from starting. The rest broke a feature when it was used.

- **First-run setup crashed.** `init_database.py` passed a string to
  `connect_db`. `connect_db` now accepts a string or a `Path`. Covered by
  `tests/test_modules.py`. Fixed in `65ee7d4`.
- **The web server crashed on startup** when it checked the admin password.
  `web/__main__.py` imported a module by its old flat name, a regression from
  the package reorganisation. Fixed in `65ee7d4`. A scan of the whole code for
  flat imports is clean.
- **The holdings-to-sheet comparison crashed on any discrepancy** (this feature was removed with the Google Sheets integration). The summary
  used the format `{delta:+.g}`, which Python rejects because `.g` needs a
  precision. The GUI had the same string. Both are fixed.
- **The web API could not update a fund's balance.** The service passed
  `nav` and `balance_date` to the database function in the wrong order, so
  every call failed. Fixed, with a test through the HTTP endpoint.
- **Hargreaves Lansdown report totals were read wrongly when they had pence.**
  The total pattern accepted whole numbers only, so `12,345.67` was read as
  `67`. Fixed. The layout of the cash balances in these reports is an
  assumption taken from the parser. It has not been checked against a real
  report.
- **Bad command-line input produced tracebacks.** An invalid `--trade-date`
  now gives a one-line message. `--fund-add` now rejects owners that are not
  in `settings.json`, as the web API already did.
- **`datetime.utcnow()` was used throughout the database layer.** It is
  deprecated. Replaced with timezone-aware UTC time, which stores the same
  text.

## Items that were open and are now closed

| Severity | Item | How it was closed | Commit |
| --- | --- | --- | --- |
| Medium | The app held a Google service-account key and read a spreadsheet with it. | The Google Sheets integration was removed: the code, the `gspread` and `google-auth` dependencies, the Docker key mount, and the `.env` settings. Holdings now come from a local CSV. The ignore rules for service-account keys stay as a safety net. | `this update` |
| Medium | Session cookie was not `Secure` by default. | `WEB_SECURE_COOKIES` defaults to `true`. The test suite runs over plain HTTP and sets it to `false` for itself. A subprocess test confirms the code default is `true`. Plain-HTTP local use needs `WEB_SECURE_COOKIES=false` in `.env`. | `07630f0` |
| Low | No CSRF protection beyond `SameSite=Lax`. | A middleware refuses state-changing `/api/` calls unless they send `Content-Type: application/json`. A cross-site HTML form cannot send that type. It also refuses any request whose `Origin` header is not this server or `WEB_PUBLIC_URL`. Tests cover both refusals and a same-origin success. The front end always sends the JSON header. | `07630f0` |
| Low | Minimum password length was 8. | Raised to 12 in the password command, both API models, and the front end. Tests cover the boundary. | `07630f0`, `484e067` |
| Low | `trade_gui.py` installed `squarify` with `pip` at runtime. | Removed. The GUI reports the missing package and points to `requirements.txt`. | `9e4cf7d` |
| Low | Twelve silent `except` blocks (bandit B110/B112). | Each one was reviewed. All are fallbacks for optional data: Yahoo quotes and FX rates, the Google Sheets prefetch, and the opening-cash sync. None makes a security decision. A failure only leaves a figure stale or missing. Logging them would help reliability, but it is not a security fix. | `9e4cf7d` (doc only; code unchanged) |
| Info | Email and report HTML. | Every HTML fragment that contains a name, ticker, note or heading goes through `html.escape`. Numbers and colours come from code constants. The check was done by reading the fragments that contain data, not by a full line-by-line review of every template. | `9e4cf7d` (doc only; code unchanged) |

## Items accepted with a condition

| Severity | Item | Condition |
| --- | --- | --- |
| Low | Rate-limit counters live in process memory and are not shared between workers. | Accepted while the app runs as one process. The container and `python -m web` both start a single uvicorn process. If you add workers, move the counters to a shared store first. |

## Pending

| Item | What is needed |
| --- | --- |
| The Docker image has not been built or inspected. | Start Docker Desktop, then run `docker build -t portfolio-manager .`. Check the image with `docker run --rm --entrypoint sh portfolio-manager -c "ls -la /app"`. The listing should contain no `.env`, `settings.json`, databases or statements, and the process should run as user `app`. |
| The Yahoo Finance integration has not been run against the live service. | Run the quote commands once. The tests cover the code paths with a fake, which shows the logic works on the shape it returns. It does not prove the live service still returns that shape. |

## Verification (this update)

- Unclosed connections: the cause was not this project's code. The real
  `yfinance` library opened SQLite connections for its timezone cache when a
  few tests reached it. The test suite now replaces Yahoo Finance for every
  test, and with `ResourceWarning` treated as an error the suite passes with no
  unclosed-connection warnings.

- `pytest`: 373 passed, with `ResourceWarning` treated as an error.
- Line coverage: 81.8% (6,606 of 8,074 statements). `trade_gui.py` is excluded
  because it needs a display. Counting it, coverage is about 67%.
- The remaining gaps are mostly less common importer layouts and the market
  movers and fund-cash reconciliation code. The portfolio card widgets are
  also untested, apart from their pure helpers and drawing logic.
- `pip-audit -r requirements.txt`: no known vulnerabilities.
- `bandit` at medium severity and above: no findings. The B104 bind-address
  finding was fixed in `1733849` and `2a7654c`. The remaining B105 flag is the
  empty `LOCKED_PASSWORD_HASH` sentinel, which is not a credential.
- The XSS guard was run against the original `app.js`: 73 unescaped
  interpolations. Against the current file: none.
