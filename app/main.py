from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .auth import (
    LoginGuard,
    PageAuthDep,
    SessionDep,
    client_ip,
    hash_password,
    validate_new_password,
    verify_password_hash,
    verify_username,
)
from .awg import AwgClient
from .collector import Collector
from .config import get_settings
from .db import Database

log = logging.getLogger("awg-stats")

BASE_DIR = Path(__file__).resolve().parent
ASSET_VERSION = "13"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["asset_v"] = ASSET_VERSION

VALID_PERIODS = {"day", "week", "month", "total"}


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    return body


def _required_key(body: dict) -> str:
    value = body.get("public_key")
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail="public_key is required")
    return value.strip()


def create_app() -> FastAPI:
    settings = get_settings()

    db = Database(settings.database_path, settings.stats_timezone)
    awg = AwgClient(settings)
    collector = Collector(settings, db, awg)
    login_guard = LoginGuard(settings.login_max_attempts, settings.login_lockout_sec)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await db.connect()
        # Bootstrap bcrypt hash from ADMIN_PASSWORD once; later UI changes own the DB.
        try:
            if not await db.get_admin_password_hash():
                await db.ensure_admin_password_hash(
                    hash_password(settings.admin_password)
                )
                log.info("seeded admin password hash from ADMIN_PASSWORD")
        except Exception:  # noqa: BLE001
            log.exception("failed to seed admin password hash")
            raise
        collector.start()
        try:
            await collector.scrape_once()
        except Exception:  # noqa: BLE001
            log.exception("initial scrape failed")
        yield
        await collector.stop()
        await db.close()

    app = FastAPI(title="Amnezia Usage Dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="awg_stats_session",
        max_age=settings.session_max_age_sec,
        same_site="lax",
        https_only=settings.session_https_only,
    )

    @app.middleware("http")
    async def cache_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/") or path == "/favicon.ico":
            response.headers.setdefault(
                "Cache-Control", "public, max-age=31536000, immutable"
            )
        elif "text/html" in response.headers.get("content-type", ""):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.state.settings = settings
    app.state.db = db
    app.state.awg = awg
    app.state.collector = collector

    app.mount(
        "/static",
        StaticFiles(directory=str(BASE_DIR / "static")),
        name="static",
    )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(
            BASE_DIR / "static" / "favicon.png",
            media_type="image/png",
        )

    async def _clients_with_disabled() -> list[dict]:
        live = collector.latest_peers
        peers_db = {p["public_key"]: p for p in await db.list_peers()}
        clients = []
        for p in live:
            st = peers_db.get(p["public_key"], {})
            clients.append(
                {
                    **p,
                    "disabled": bool(st.get("disabled")),
                    "lifetime_rx": int(
                        st["lifetime_rx"] if "lifetime_rx" in st else p["rx"]
                    ),
                    "lifetime_tx": int(
                        st["lifetime_tx"] if "lifetime_tx" in st else p["tx"]
                    ),
                }
            )
        live_keys = {p["public_key"] for p in live}
        for pk, st in peers_db.items():
            if pk not in live_keys:
                clients.append(
                    {
                        "public_key": pk,
                        "name": st["name"],
                        "allowed_ips": st["allowed_ips"],
                        "endpoint": None,
                        "handshake": 0,
                        "rx": st["last_rx"],
                        "tx": st["last_tx"],
                        "lifetime_rx": st["lifetime_rx"],
                        "lifetime_tx": st["lifetime_tx"],
                        "online": False,
                        "status": "offline",
                        "disabled": bool(st["disabled"]),
                        "rx_bps": 0,
                        "tx_bps": 0,
                        "up_bps": 0,
                        "down_bps": 0,
                    }
                )
        return clients

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if request.session.get("auth"):
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        ip = client_ip(request)
        retry_after = login_guard.retry_after(ip)
        if retry_after:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"error": f"Too many attempts. Try again in {(retry_after + 59) // 60} min."},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        stored_hash = await db.get_admin_password_hash()
        if verify_username(settings, username) and verify_password_hash(
            password, stored_hash
        ):
            login_guard.reset(ip)
            request.session["auth"] = True
            return RedirectResponse("/", status_code=303)
        login_guard.record_failure(ip)
        log.warning("failed login from %s", ip)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password"},
            status_code=401,
        )

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/account", response_class=HTMLResponse)
    async def account_page(request: Request, _: PageAuthDep):
        return templates.TemplateResponse(
            request,
            "account.html",
            {
                "active": "account",
                "error": None,
                "success": None,
                "admin_user": settings.admin_user,
            },
        )

    @app.post("/account/password", response_class=HTMLResponse)
    async def account_change_password(
        request: Request,
        _: PageAuthDep,
        current_password: str = Form(...),
        new_password: str = Form(...),
        confirm_password: str = Form(...),
    ):
        ctx = {
            "active": "account",
            "error": None,
            "success": None,
            "admin_user": settings.admin_user,
        }
        ip = client_ip(request)
        retry_after = login_guard.retry_after(ip)
        if retry_after:
            ctx["error"] = (
                f"Too many attempts. Try again in {(retry_after + 59) // 60} min."
            )
            return templates.TemplateResponse(
                request,
                "account.html",
                ctx,
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        stored_hash = await db.get_admin_password_hash()
        if not verify_password_hash(current_password, stored_hash):
            login_guard.record_failure(ip)
            log.warning("failed password change (bad current) from %s", ip)
            ctx["error"] = "Current password is incorrect."
            return templates.TemplateResponse(
                request, "account.html", ctx, status_code=400
            )

        if new_password != confirm_password:
            ctx["error"] = "New password and confirmation do not match."
            return templates.TemplateResponse(
                request, "account.html", ctx, status_code=400
            )

        if new_password == current_password:
            ctx["error"] = "New password must be different from the current one."
            return templates.TemplateResponse(
                request, "account.html", ctx, status_code=400
            )

        policy_error = validate_new_password(new_password)
        if policy_error:
            ctx["error"] = policy_error
            return templates.TemplateResponse(
                request, "account.html", ctx, status_code=400
            )

        try:
            new_hash = hash_password(new_password)
        except ValueError as exc:
            ctx["error"] = str(exc)
            return templates.TemplateResponse(
                request, "account.html", ctx, status_code=400
            )

        await db.set_admin_password_hash(new_hash)
        login_guard.reset(ip)
        log.info("admin password changed from %s", ip)
        ctx["success"] = (
            "Password updated. It is stored as a bcrypt hash in the database "
            "(not plaintext). ADMIN_PASSWORD in .env is only used for the first boot."
        )
        return templates.TemplateResponse(request, "account.html", ctx)

    @app.get("/", response_class=HTMLResponse)
    async def overview(request: Request, _: PageAuthDep):
        return templates.TemplateResponse(
            request, "overview.html", {"active": "overview"}
        )

    @app.get("/clients", response_class=HTMLResponse)
    async def clients_page(request: Request, _: PageAuthDep):
        return templates.TemplateResponse(
            request, "clients.html", {"active": "clients"}
        )

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request, _: PageAuthDep):
        return templates.TemplateResponse(
            request, "history.html", {"active": "history"}
        )

    @app.get("/performance", response_class=HTMLResponse)
    async def performance_page(request: Request, _: PageAuthDep):
        return templates.TemplateResponse(
            request, "performance.html", {"active": "performance"}
        )

    @app.get("/quotas", response_class=HTMLResponse)
    async def quotas_page(request: Request, _: PageAuthDep):
        return templates.TemplateResponse(
            request, "quotas.html", {"active": "quotas"}
        )

    @app.get("/api/overview")
    async def api_overview(_: SessionDep):
        today = db.today()
        day_rows = await db.usage_between(today, today)
        week_rows = await db.usage_between(today - timedelta(days=6), today)
        month_rows = await db.usage_between(today.replace(day=1), today)

        def sum_total(rows: list) -> int:
            return sum(int(r["rx"] or 0) + int(r["tx"] or 0) for r in rows)

        clients = await _clients_with_disabled()
        return {
            "online": sum(1 for p in clients if p["online"]),
            "today_bytes": sum_total(day_rows),
            "week_bytes": sum_total(week_rows),
            "month_bytes": sum_total(month_rows),
            "total_bytes": sum(
                int(c.get("lifetime_rx") or 0) + int(c.get("lifetime_tx") or 0)
                for c in clients
            ),
            "clients": clients,
            "series": await db.daily_series(30),
            "performance": collector.performance.latest,
            "collector": {
                "last_ok_at": collector.last_ok_at.isoformat()
                if collector.last_ok_at
                else None,
                "last_error": collector.last_error,
                "mode": settings.awg_mode,
                "timezone": settings.stats_timezone,
            },
        }

    @app.get("/api/htop")
    async def api_htop(_: SessionDep):
        # Live snapshot for the Overview htop panel (~1s client poll).
        return await collector.performance.refresh()

    @app.get("/api/history")
    async def api_history(_: SessionDep, days: int = 30):
        days = max(1, min(days, 365))
        today = db.today()
        start = today - timedelta(days=days - 1)
        period_rows = await db.usage_between(start, today)
        period_by_key = {
            r["public_key"]: {"rx": int(r["rx"] or 0), "tx": int(r["tx"] or 0)}
            for r in period_rows
        }
        peers = await db.list_peers()
        live_names = {p["public_key"]: p["name"] for p in collector.latest_peers}
        by_client = []
        seen: set[str] = set()
        for peer in peers:
            pk = peer["public_key"]
            seen.add(pk)
            usage = period_by_key.get(pk, {"rx": 0, "tx": 0})
            by_client.append(
                {
                    "public_key": pk,
                    "name": live_names.get(pk) or peer["name"],
                    "rx": usage["rx"],
                    "tx": usage["tx"],
                    "lifetime_rx": int(peer["lifetime_rx"] or 0),
                    "lifetime_tx": int(peer["lifetime_tx"] or 0),
                }
            )
        for pk, usage in period_by_key.items():
            if pk in seen:
                continue
            by_client.append(
                {
                    "public_key": pk,
                    "name": live_names.get(pk) or pk[:8],
                    "rx": usage["rx"],
                    "tx": usage["tx"],
                    "lifetime_rx": 0,
                    "lifetime_tx": 0,
                }
            )
        by_client.sort(
            key=lambda r: (
                -(r["rx"] + r["tx"]),
                -(r["lifetime_rx"] + r["lifetime_tx"]),
                r["name"].lower(),
            )
        )
        return {
            "series": await db.daily_series(days),
            "by_client": by_client,
            "since": start.isoformat(),
            "note": (
                "The 7 / 30 / 90 day columns are what this panel recorded. "
                "All time is since the client first appeared "
                "(survives AmneziaWG restarts). "
                f"Days follow {settings.stats_timezone} midnight."
            ),
        }

    @app.get("/api/quotas")
    async def api_quotas_list(_: SessionDep):
        quotas = await db.list_quotas()
        usage_cache: dict[str, dict[str, dict]] = {}
        result = []
        for q in quotas:
            period = q["period"]
            if period not in usage_cache:
                usage_cache[period] = await db.totals_for_period(period)
            used = usage_cache[period].get(
                q["public_key"], {"rx": 0, "tx": 0, "total": 0}
            )
            peer = await db.get_peer(q["public_key"])
            result.append(
                {
                    **q,
                    "used_bytes": used["total"],
                    "disabled": bool(peer["disabled"]) if peer else False,
                }
            )
        return {"quotas": result, "peers": await db.list_peers()}

    @app.post("/api/quotas")
    async def api_quotas_upsert(request: Request, _: SessionDep):
        body = await _json_body(request)
        public_key = _required_key(body)
        peer = await db.get_peer(public_key)
        name = body.get("name") or (peer["name"] if peer else public_key[:8])
        try:
            limit_gb = float(body.get("limit_gb", 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="limit_gb must be a number")
        if not limit_gb > 0:
            raise HTTPException(status_code=400, detail="limit_gb must be > 0")
        period = body.get("period", "month")
        if period not in VALID_PERIODS:
            raise HTTPException(status_code=400, detail="invalid period")
        await db.upsert_quota(
            public_key=public_key,
            name=str(name),
            limit_bytes=int(limit_gb * 1024**3),
            period=period,
            enabled=bool(body.get("enabled", True)),
            auto_disable=bool(body.get("auto_disable", True)),
        )
        return {"ok": True}

    @app.delete("/api/quotas")
    async def api_quotas_delete(request: Request, _: SessionDep):
        body = await _json_body(request)
        await db.delete_quota(_required_key(body))
        return {"ok": True}

    @app.post("/api/peers/enable")
    async def api_peer_enable(request: Request, _: SessionDep):
        body = await _json_body(request)
        public_key = _required_key(body)
        peer = await db.get_peer(public_key)
        if not peer:
            raise HTTPException(status_code=404, detail="peer not found")
        allowed_ips = peer["allowed_ips"]
        if not allowed_ips:
            # A blanket 0.0.0.0/0 here would hijack routing for every peer.
            raise HTTPException(
                status_code=409,
                detail="allowed-ips unknown for this peer; re-add it in Amnezia first",
            )
        await awg.enable_peer(public_key, allowed_ips)
        await db.set_disabled(public_key, False)
        return {"ok": True}

    @app.post("/api/peers/disable")
    async def api_peer_disable(request: Request, _: SessionDep):
        body = await _json_body(request)
        public_key = _required_key(body)
        peer = await db.get_peer(public_key)
        if not peer:
            raise HTTPException(status_code=404, detail="peer not found")
        await awg.disable_peer(public_key)
        await db.set_disabled(public_key, True, by="manual")
        return {"ok": True}

    @app.post("/api/scrape")
    async def api_scrape(_: SessionDep):
        await collector.scrape_once()
        return {"ok": True, "error": collector.last_error}

    return app


app = create_app()
