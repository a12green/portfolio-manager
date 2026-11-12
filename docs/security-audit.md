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

## Open items

These need a decision or a deployment setting. I did not change them without
asking.

| Severity | Item | Recommendation |
| --- | --- | --- |
| Medium | **Session cookie is not marked `Secure` by default** (`WEB_SECURE_COOKIES=false`). Over plain HTTP the session can be read on the network. | Set `WEB_SECURE_COOKIES=true` for any deployment reached over HTTPS, which includes PythonAnywhere. The default stays `false` so local HTTP use still works. Consider flipping the default. |
| Low | **No CSRF tokens.** Protection relies on `SameSite=Lax` cookies and endpoints that accept only JSON bodies. Not tested with a real cross-site request. | Acceptable for now. Add CSRF tokens if the app is ever exposed to untrusted sites. |
| Low | **Rate-limit counters live in process memory.** They reset on restart and are not shared between workers. | Fine for a single-process deployment. Use a shared store if you run several workers. |
| Low | **Minimum password length is 8 characters.** | Consider 12 for an app that holds financial data. |
| Low | **`trade_gui.py` installs `squarify` with `pip` at runtime** if it is missing. | Remove the runtime install. The package is already in `requirements.txt`. |
| Low | **Twelve `except: pass` / `except: continue` blocks** (bandit B110, B112). They can hide failures. | Review each one. None is an exploit path. |
| Info | Email and report HTML is escaped in the trade report and reviewed by sampling in the holdings email. It was not reviewed line by line. | Review the email templates in full before sending them to anyone else. |
| Info | Password-reset emails are sent only to an address stored on the account. SMTP credentials come from `.env`. | No change needed. Keep `.env` out of git and out of the image. |

## Verification

- `pytest`: 80 passed. Line coverage is about 42%.
- `pip-audit -r requirements.txt`: no known vulnerabilities.
- `bandit` (medium and above): one finding, the bind address, now fixed. The
  remaining `B105` flag is the empty `LOCKED_PASSWORD_HASH` sentinel, which is
  not a credential.
- The XSS guard was run against the original `app.js`: 73 unescaped
  interpolations found. Against the current file: 0.
