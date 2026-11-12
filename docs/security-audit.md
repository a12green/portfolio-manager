# Security audit

**Scope:** the whole repository at this commit: the FastAPI web UI and its
authentication, the browser front end (`web/static/app.js`), the importers,
the reports and email code, the desktop GUI's dependency handling, the
Dockerfile and `.dockerignore`, and the dependency pins.

**Method**

- Manual code review of the web layer, authentication and session handling,
  the front end's use of `innerHTML`, SQL construction, and file and secret
  handling.
- `bandit` static analysis (medium severity and above, plus lower-severity
  patterns reviewed by hand).
- `pip-audit` against the pinned requirements.
- A test suite of 80 tests, including regression tests for each fix below.

**Not done:** a live penetration test, a browser-based XSS test, and a
`docker build`. Docker was not run in this environment.

## Findings fixed

| Severity | Finding | Fix | Commit |
| --- | --- | --- | --- |
| High | **Stored cross-site scripting in the web UI.** Fund names, tickers, notes, owner names and market-mover names were inserted into `innerHTML` unescaped. A value saved by one owner could run script in the admin's session. The original file had 73 unescaped interpolations. | Every interpolated value in markup goes through `esc()`. `tests/test_frontend_escaping.py` fails if one is missed. It fails on the original file and passes now. | `bf8478b` |
| High | **Sign-in lockout bypass.** The rate limiter trusted `X-Forwarded-For` from any caller, so rotating that header gave each attempt a fresh bucket. | The header is used only when `WEB_TRUST_PROXY=true`, which should be set only behind a reverse proxy you run. | `52d42cf` |
| High | **Secrets and data could be baked into the Docker image.** There was no `.dockerignore`, so `COPY . .` included `.env`, `settings.json`, databases and statements. | `.dockerignore` excludes them. | `1733849` |
| High | **Known-vulnerable dependency.** `pillow` 12.3.0 had 40 advisories, fixed in 13.0.0. | Pinned `pillow==13.0.0` and `ttkbootstrap==2.2.7` (the newer release accepts pillow 13). `pip-audit` reports no known vulnerabilities. | `1733849` and later |
| Medium | **No security headers.** There was no Content-Security-Policy, framing protection or `nosniff`. | Every response now sets CSP, `X-Frame-Options: DENY`, `nosniff`, a referrer policy and a permissions policy. HSTS is sent when secure cookies are on. | `38d0a59` |
| Medium | **Container ran as root.** | The image runs as an unprivileged `app` user. Only `/data` and `/app/reports` are writable. | `1733849` |
| Medium | **Server bound to all interfaces by default** (bandit B104). | Default `WEB_HOST` is `127.0.0.1` in both the web entry point and `main.py --serve-web`. Docker sets `0.0.0.0` itself. | `1733849` and later |
| Medium | **Unpinned dependencies**, and an unused `slowapi` dependency. | All direct dependencies are pinned. `slowapi` is removed. | `1733849` |
| Low | **Usernames could be found by timing.** An unknown username skipped the bcrypt check, so it answered faster. | An unknown username runs the same bcrypt check. | `52d42cf` |
| Low | **API responses could be cached by browsers.** | Every `/api/` response is `Cache-Control: no-store`. | `38d0a59` |
| Low | **Real account numbers and the account holder's name were in the source.** | Moved to `.env` (`FIDELITY_OWNER_B_HOLDER`, `FIDELITY_OWNER_B_ACCOUNT`, `FIDELITY_LLC_ACCOUNT_IDS`). Scanned all tracked files: none remain. | earlier commits |

## Bugs found during testing (not security issues, but they stopped the app)

- **First-run setup crashed.** `init_database.py` passed a string to
  `connect_db`. `connect_db` now accepts either type. Covered by
  `tests/test_modules.py`.
- **The web server crashed on startup** when it checked the admin password.
  `web/__main__.py` imported a module by its old flat name. This was introduced
  by the package reorganisation. The bad import is fixed, and a flat-import
  scan of the whole code is clean.

## Items that were open and are now closed

| Severity | Item | How it was closed |
| --- | --- | --- |
| Medium | Session cookie was not `Secure` by default. | `WEB_SECURE_COOKIES` now defaults to `true`. The test suite runs over plain HTTP and sets it to `false` for itself. A subprocess test confirms the code default is `true`. Plain-HTTP local use needs `WEB_SECURE_COOKIES=false` in `.env`. |
| Low | No CSRF protection beyond `SameSite=Lax`. | A middleware refuses state-changing `/api/` calls unless they send `Content-Type: application/json`, which a cross-site HTML form cannot do. It also refuses any request whose `Origin` header is not this server or `WEB_PUBLIC_URL`. Tests cover both refusals and a same-origin success. The front end now always sends the JSON header. |
| Low | Minimum password length was 8. | Raised to 12 in the password command, both API models, and the front end. Tests cover the boundary. |
| Low | `trade_gui.py` installed `squarify` with `pip` at runtime. | Removed. The GUI now reports the missing package and points to `requirements.txt`. |
| Low | Twelve silent `except` blocks (bandit B110/B112). | Reviewed each one. All are fallbacks for optional data: Yahoo quotes and FX rates, the Google Sheets prefetch, and the opening-cash sync. None makes a security decision. Losing one only leaves a figure stale or missing. Adding logging to them is a reliability improvement, not a security fix. |
| Info | Email and report HTML was reviewed by sampling. | Reviewed in full. Every user-derived value in the holdings email, trade report and strike-alert email goes through `html.escape`. The remaining interpolations are numbers and colours from code constants. |

## Items accepted with a condition

| Severity | Item | Condition |
| --- | --- | --- |
| Low | Rate-limit counters live in process memory and are not shared between workers. | Accepted while the app runs as one process. The container and `python -m web` both start a single uvicorn process. If you add workers, move the counters to a shared store first. |

## Still pending

| Item | What is needed |
| --- | --- |
| The Docker image has not been built or inspected. | Start Docker Desktop, then run `docker build -t portfolio-manager .` and check the image with `docker run --rm --entrypoint sh portfolio-manager -c "ls -la /app"`. The listing should contain no `.env`, `settings.json`, databases or statements, and the process should run as user `app`. |

## Verification

- `pytest`: 86 passed. Line coverage is about 42%.
- `pip-audit -r requirements.txt`: no known vulnerabilities.
- `bandit` (medium and above): one finding, the bind address, now fixed. The
  remaining `B105` flag is the empty `LOCKED_PASSWORD_HASH` sentinel, which is
  not a credential.
- The XSS guard was run against the original `app.js`: 73 unescaped
  interpolations found. Against the current file: 0.
