"""FastAPI application — portfolio manager web UI."""

from __future__ import annotations

from config import OWNER_A
import logging
import os
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ledger.db import connect_db
from web import auth, reset_mail, services
from web.auth import (
    SECURE_COOKIES,
    SESSION_COOKIE,
    WebUser,
    authenticate_user,
    clear_session_cookie,
    create_session,
    delete_session,
    resolve_owner,
    seed_default_users,
    set_session_cookie,
    user_for_session_token,
)
from ledger.trades_db import init_trade_schema

load_dotenv()

STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


def _db_conn():
    import trades_cli

    conn = connect_db(trades_cli._db_path())
    try:
        init_trade_schema(conn)
        seed_default_users(conn)
        yield conn
    finally:
        conn.close()


app = FastAPI(
    title="Portfolio Manager",
    description="Web UI for trade ledger, portfolio holdings, and market data.",
    version="1.0.0",
)


def get_current_user(request: Request, conn=Depends(_db_conn)) -> WebUser:
    token = request.cookies.get(SESSION_COOKIE)
    user = user_for_session_token(conn, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class ProfileBody(BaseModel):
    email: str = Field(default="", max_length=254)
    display_name: str = Field(default="", max_length=80)
    # Required only when the email address is being added, changed or removed.
    current_password: str | None = Field(default=None, max_length=200)


class ForgotPasswordBody(BaseModel):
    email: str = Field(min_length=1, max_length=254)


class ResetPasswordBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class FundCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    owner: str = Field(min_length=1, max_length=40)
    opening_cash: float = 0.0
    currency: str = "USD"


class TradeCreate(BaseModel):
    fund_name: str
    ticker: str = ""
    side: str
    shares: float = Field(gt=0)
    price: float = Field(gt=0)
    trade_date: date | None = None
    fees: float = 0.0
    notes: str = ""
    instrument_type: str = "stock"
    underlying: str | None = None
    expiry: date | None = None
    strike: float | None = None
    put_call: str | None = None
    contract_multiplier: float | None = None


class FundBalanceUpdate(BaseModel):
    nav: float = Field(gt=0)
    balance_date: date | None = None


class OtherAssetCreate(BaseModel):
    asset_type: str = Field(min_length=1, max_length=80)
    amount: float = Field(gt=0)
    currency: str = "USD"


_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    headers = response.headers
    headers["Content-Security-Policy"] = _CSP
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "same-origin"
    headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/"):
        # Financial data must not be kept by browsers or shared caches.
        headers["Cache-Control"] = "no-store"
    if SECURE_COOKIES:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _expected_origins(request: Request) -> set[str]:
    """Origins allowed to make state-changing requests: this server, and WEB_PUBLIC_URL."""
    from urllib.parse import urlsplit

    own = urlsplit(str(request.base_url))
    origins = {f"{own.scheme}://{own.netloc}"}
    public = urlsplit(os.getenv("WEB_PUBLIC_URL", "").strip())
    if public.scheme and public.netloc:
        origins.add(f"{public.scheme}://{public.netloc}")
    return origins


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Refuse cross-site writes. Two checks, either of which blocks a classic form-based CSRF:
    a state-changing API call must send a JSON content type, which a plain HTML form cannot,
    and if the browser sends an Origin header it must be this app's own origin."""
    if request.method in _UNSAFE_METHODS and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in _expected_origins(request):
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            return JSONResponse({"detail": "Expected a JSON request"}, status_code=415)
    return await call_next(request)


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response, conn=Depends(_db_conn)):
    auth._check_rate_limit(request)
    user = authenticate_user(conn, body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    auth._clear_rate_limit(request)
    token = create_session(conn, user.id)
    set_session_cookie(response, token)
    return {
        "username": user.username,
        "owner_scope": user.owner_scope,
        "is_admin": user.is_admin,
        "owners": auth.allowed_owner_names(user),
    }


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, conn=Depends(_db_conn)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(conn, token)
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(user: WebUser = Depends(get_current_user)):
    return {
        "username": user.username,
        "owner_scope": user.owner_scope,
        "is_admin": user.is_admin,
        "owners": auth.allowed_owner_names(user),
    }


@app.post("/api/auth/change-password")
def change_password(
    body: ChangePasswordBody,
    request: Request,
    user: WebUser = Depends(get_current_user),
    conn=Depends(_db_conn),
):
    if body.new_password == body.current_password:
        raise HTTPException(
            status_code=400, detail="New password must be different from the current one"
        )
    try:
        auth.change_password(conn, user, body.current_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Password changed — sign the account out everywhere except this session.
    auth.delete_other_sessions(conn, user.id, request.cookies.get(SESSION_COOKIE))
    return {"ok": True}


@app.get("/api/auth/profile")
def get_profile(user: WebUser = Depends(get_current_user), conn=Depends(_db_conn)):
    return {"username": user.username, **auth.get_profile(conn, user.id)}


@app.put("/api/auth/profile")
def put_profile(
    body: ProfileBody,
    user: WebUser = Depends(get_current_user),
    conn=Depends(_db_conn),
):
    current = auth.get_profile(conn, user.id)
    raw_email = body.email.strip()
    try:
        email = auth.normalize_email(raw_email) if raw_email else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if email != current["email"]:
        # Changing the reset address is security-sensitive: anyone holding a
        # session must not be able to redirect password resets silently.
        if not body.current_password:
            raise HTTPException(
                status_code=400,
                detail="Enter your current password to change your email address",
            )
        if not auth.verify_current_password(conn, user, body.current_password):
            raise HTTPException(status_code=400, detail="Current password is incorrect")

    try:
        auth.set_profile(conn, user.id, email, body.display_name.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "username": user.username,
        "email": email,
        "display_name": body.display_name.strip(),
    }


_FORGOT_REPLY = {
    "ok": True,
    "message": "If that email address is registered, a reset link is on its way.",
}


@app.post("/api/auth/forgot-password")
def forgot_password(body: ForgotPasswordBody, request: Request, conn=Depends(_db_conn)):
    auth._check_rate_limit(request, bucket=auth._RESET_ATTEMPTS, max_attempts=auth._RESET_MAX_ATTEMPTS)
    # Same reply whether or not the address exists, so this can't be used to probe accounts.
    try:
        email = auth.normalize_email(body.email)
    except ValueError:
        return _FORGOT_REPLY
    user = auth.find_user_by_email(conn, email)
    if user is None:
        return _FORGOT_REPLY
    token = auth.create_password_reset(conn, user.id)
    try:
        reset_mail.send_password_reset_email(
            to=email,
            username=user.username,
            token=token,
            minutes=auth.RESET_TOKEN_MINUTES,
        )
    except Exception as e:  # noqa: BLE001 - never leak SMTP details to the browser
        # Do not log the address itself.
        logger.error("Password reset email failed for user id %s: %s", user.id, type(e).__name__)
    return _FORGOT_REPLY


@app.post("/api/auth/reset-password")
def reset_password(body: ResetPasswordBody, request: Request, conn=Depends(_db_conn)):
    auth._check_rate_limit(request, bucket=auth._RESET_ATTEMPTS, max_attempts=auth._RESET_MAX_ATTEMPTS)
    try:
        auth.reset_password_with_token(conn, body.token, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@app.get("/api/health")
def health():
    """Unauthenticated liveness check."""
    return {"status": "ok", "time": datetime.now().isoformat(timespec="seconds")}


@app.get("/api/meta")
def meta(user: WebUser = Depends(get_current_user)):
    return services.meta_payload(user)


@app.get("/api/funds")
def get_funds(user: WebUser = Depends(get_current_user)):
    return {"funds": services.list_funds_payload(user)}


@app.post("/api/funds")
def post_fund(body: FundCreate, user: WebUser = Depends(get_current_user)):
    auth.assert_owner_field(user, body.owner)
    try:
        fund = services.create_fund_payload(
            body.name, body.owner, body.opening_cash, body.currency.upper(), user=user
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return fund


@app.get("/api/funds/{fund_name}/trades")
def get_trades(fund_name: str, user: WebUser = Depends(get_current_user)):
    try:
        return {"trades": services.list_trades_payload(fund_name, user=user)}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.post("/api/trades")
def post_trade(body: TradeCreate, user: WebUser = Depends(get_current_user)):
    if body.side.lower() not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="side must be buy or sell")
    try:
        trade = services.add_trade_payload(
            body.fund_name,
            body.ticker,
            body.side,
            body.shares,
            body.price,
            body.trade_date,
            body.fees,
            body.notes or None,
            user=user,
            instrument_type=body.instrument_type,
            underlying=body.underlying,
            expiry=body.expiry,
            strike=body.strike,
            put_call=body.put_call,
            contract_multiplier=body.contract_multiplier,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return trade


@app.get("/api/portfolio")
def get_portfolio(
    owner: str = OWNER_A,
    fund: str = "All",
    market: str = "All",
    live: bool = False,
    user: WebUser = Depends(get_current_user),
):
    owner = resolve_owner(user, owner)
    try:
        return services.portfolio_payload(
            owner=owner, fund=fund, market=market, live=live
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal error") from None


@app.get("/api/ledger")
def get_ledger(
    owner: str = OWNER_A,
    fund: str = "All",
    live: bool = False,
    user: WebUser = Depends(get_current_user),
):
    owner = resolve_owner(user, owner)
    try:
        return services.ledger_payload(owner=owner, fund=fund, live=live)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal error") from None


@app.get("/api/heatmap")
def get_heatmap(
    owner: str = "All",
    fund: str = "All",
    color_by: str = "overall",
    live: bool = False,
    user: WebUser = Depends(get_current_user),
):
    owner = resolve_owner(user, owner)
    try:
        return services.heatmap_payload(
            owner=owner, fund=fund, color_by=color_by, live=live
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal error") from None


@app.get("/api/market-movers")
def get_market_movers(market: str = "United States", user: WebUser = Depends(get_current_user)):
    try:
        return services.movers_payload(market)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal error") from None


@app.get("/api/fund-balances")
def get_fund_balances(
    owner: str = OWNER_A,
    user: WebUser = Depends(get_current_user),
):
    owner = resolve_owner(user, owner)
    try:
        return services.fund_balances_payload(owner)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal error") from None


@app.put("/api/funds/{fund_name}/balance")
def update_fund_balance(
    fund_name: str,
    body: FundBalanceUpdate,
    user: WebUser = Depends(get_current_user),
):
    try:
        from ledger.trades_db import get_fund_by_name
        
        conn = services.connect_db(services.trades_cli._db_path())
        try:
            services.init_trade_schema(conn)
            fund = get_fund_by_name(conn, fund_name)
            if not fund:
                raise HTTPException(status_code=404, detail=f"Unknown fund {fund_name!r}")
            from web.auth import assert_fund_access
            assert_fund_access(user, fund)
        finally:
            conn.close()
        
        balance_date = body.balance_date or date.today()
        return services.update_fund_manual_balance(fund_name, body.nav, balance_date)
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 - log server-side, never leak the internal message
        logger.exception("Fund balance update failed")
        raise HTTPException(status_code=500, detail="Internal error") from e


@app.get("/api/net-worth")
def get_net_worth(
    owner: str = OWNER_A,
    user: WebUser = Depends(get_current_user),
):
    owner = resolve_owner(user, owner)
    try:
        return services.net_worth_payload(owner)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal error") from None


@app.get("/api/other-assets")
def get_other_assets(
    owner: str = OWNER_A,
    user: WebUser = Depends(get_current_user),
):
    owner = resolve_owner(user, owner)
    try:
        return services.other_assets_payload(owner)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal error") from None


@app.post("/api/other-assets")
def create_other_asset(
    body: OtherAssetCreate,
    owner: str = OWNER_A,
    user: WebUser = Depends(get_current_user),
):
    owner = resolve_owner(user, owner)
    auth.assert_owner_field(user, owner)
    try:
        return services.create_other_asset(owner, body.asset_type, body.amount, body.currency.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal error") from None

@app.delete("/api/other-assets/{asset_type}")
def delete_other_asset(
    asset_type: str,
    owner: str = OWNER_A,
    user: WebUser = Depends(get_current_user),
):
    owner = resolve_owner(user, owner)
    auth.assert_owner_field(user, owner)
    try:
        return services.delete_other_asset(owner, asset_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal error") from None


@app.get("/api/fund-trends")
def get_fund_trends(
    owner: str = OWNER_A,
    fund: str | None = None,
    user: WebUser = Depends(get_current_user),
):
    owner = resolve_owner(user, owner)
    try:
        return services.fund_trends_payload(owner, fund)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Request failed")
        raise HTTPException(status_code=500, detail="Internal error") from None


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
