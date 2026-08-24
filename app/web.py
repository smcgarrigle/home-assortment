import asyncio
import contextlib
import logging
import secrets
import time
from pathlib import Path

from fastapi import (Depends, FastAPI, File, Form, Header, HTTPException,
                     UploadFile)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import collector, config, db
from .env_file import mask_value, read_env, write_env

log = logging.getLogger("web")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    tasks = collector.start(asyncio.get_running_loop())
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Home Sensors", lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def api_status():
    return {"now": int(time.time()), "sources": collector.status}


@app.get("/api/devices")
def api_devices():
    return db.list_devices()


@app.get("/api/history")
def api_history(device_id: int, metric: str, hours: int = 24):
    hours = max(1, min(hours, 24 * 90))
    since = int(time.time()) - hours * 3600
    # cap the series at ~600 points via time-bucket averaging
    bucket = max(60, hours * 3600 // 600)
    return db.history(device_id, metric, since, bucket)


ENERGY_SPANS = {"hour": 3, "day": 45, "week": 182, "month": 730}


def _bucket_start(ts: int, bucket: str):
    from datetime import datetime, timedelta

    d = datetime.fromtimestamp(ts)
    if bucket == "hour":
        d = d.replace(minute=0, second=0, microsecond=0)
    elif bucket == "day":
        d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    elif bucket == "week":
        d = (d - timedelta(days=d.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
    else:
        d = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(d.timestamp())


def _rate_for(hour_start: int) -> float:
    """$/kWh for the hour beginning at hour_start, per PEAK_* config."""
    from datetime import datetime

    d = datetime.fromtimestamp(hour_start)
    # E-TOU-C3 prices the peak window every day, weekends included.
    is_peak = config.PEAK_START_HOUR <= d.hour < config.PEAK_END_HOUR
    return config.PEAK_RATE_PER_KWH if is_peak else config.OFFPEAK_RATE_PER_KWH


@app.get("/api/energy")
def api_energy(device_id: int, bucket: str = "day", span_days: int | None = None):
    """kWh and cost per hour/day/week/month for one device.

    Uses imported energy_kwh rows where available; buckets without them are
    filled by integrating the live power samples (gaps capped at 5 min so
    downtime doesn't fabricate consumption). Where both exist the larger is
    used, since integration undercounts partially-covered buckets.

    Cost is computed at hourly resolution (each hour billed at the peak or
    off-peak rate for that hour) and then rolled up into the requested
    bucket, so a "day" or "week" bucket that spans both rates is still
    costed correctly.
    """
    if bucket not in ENERGY_SPANS:
        raise HTTPException(400, f"bucket must be one of {list(ENERGY_SPANS)}")
    days = min(span_days or ENERGY_SPANS[bucket], 1100)
    since = int(time.time()) - days * 86400

    metered: dict[int, float] = {}
    for ts, v in db.series(device_id, "energy_kwh", since):
        h = _bucket_start(ts, "hour")
        metered[h] = metered.get(h, 0.0) + v

    integrated: dict[int, float] = {}
    samples = db.series(device_id, "power", since)
    now = int(time.time())
    for i, (ts, watts) in enumerate(samples):
        next_ts = samples[i + 1][0] if i + 1 < len(samples) else now
        dt = min(next_ts - ts, 300)
        h = _bucket_start(ts, "hour")
        integrated[h] = integrated.get(h, 0.0) + watts * dt / 3_600_000

    hourly_kwh = {
        h: max(metered.get(h, 0.0), integrated.get(h, 0.0))
        for h in sorted(set(metered) | set(integrated))
    }

    kwh: dict[int, float] = {}
    cost: dict[int, float] = {}
    for h, v in hourly_kwh.items():
        b = _bucket_start(h, bucket)
        kwh[b] = kwh.get(b, 0.0) + v
        cost[b] = cost.get(b, 0.0) + v * _rate_for(h)

    data = [[b, round(kwh[b], 4), round(cost[b], 4)] for b in sorted(kwh)]
    return {"bucket": bucket, "data": data,
            "total_kwh": round(sum(v for _, v, _ in data), 3),
            "total_cost": round(sum(c for _, _, c in data), 2),
            "peak_rate": config.PEAK_RATE_PER_KWH,
            "offpeak_rate": config.OFFPEAK_RATE_PER_KWH}


@app.post("/api/import")
async def api_import(file: UploadFile = File(...), device_id: int = Form(...)):
    from .importer import import_csv
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    try:
        return import_csv(text, device_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# Settings / Setup
# ---------------------------------------------------------------------------

def require_settings_token(
        x_settings_token: str | None = Header(default=None)) -> None:
    """Gate the settings write endpoints on SETTINGS_TOKEN, when one is set.

    Unset is the default and leaves them open, which is only appropriate on a
    trusted network -- these endpoints can read and rewrite .env.
    """
    expected = config.SETTINGS_TOKEN
    if not expected:
        return
    if not x_settings_token or not secrets.compare_digest(x_settings_token,
                                                          expected):
        raise HTTPException(401, "Invalid or missing settings token")


@app.get("/setup")
def setup_page():
    return FileResponse(STATIC_DIR / "setup.html")


CREDENTIAL_FIELDS = {
    "GOVEE_API_KEY", "QINGPING_APP_KEY", "QINGPING_APP_SECRET",
    "GOVEE_EMAIL", "GOVEE_PASSWORD",
}
# Energy settings are read from `config` on every /api/energy request, so a
# saved change can be applied in-process. Poll intervals are captured by the
# collector loops at startup, so those still need a restart.
LIVE_TUNING = {
    "PEAK_RATE_PER_KWH": float,
    "OFFPEAK_RATE_PER_KWH": float,
    "PEAK_START_HOUR": int,
    "PEAK_END_HOUR": int,
}
RESTART_TUNING = {
    "GOVEE_POLL_SECONDS", "QINGPING_POLL_SECONDS",
    "GOVEE_IOT_POLL_SECONDS", "QINGPING_BACKFILL_DAYS",
}
TUNING_FIELDS = set(LIVE_TUNING) | RESTART_TUNING


def _validate_tuning(key: str, raw: str):
    """Coerce and range-check one tuning value, or raise HTTPException."""
    try:
        value = LIVE_TUNING[key](raw)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key}: expected a number, got {raw!r}")
    if key.endswith("_RATE_PER_KWH") and value < 0:
        raise HTTPException(400, f"{key}: rate cannot be negative")
    if key.endswith("_HOUR") and not 0 <= value <= 23:
        raise HTTPException(400, f"{key}: hour must be between 0 and 23")
    return value


@app.get("/api/settings")
def api_settings():
    """Current config with secrets masked."""
    env = read_env()
    return {
        "govee": {
            "configured": bool(env.get("GOVEE_API_KEY", "").strip()),
            "api_key_hint": mask_value("GOVEE_API_KEY",
                                       env.get("GOVEE_API_KEY", "")),
            "status": collector.status.get("govee", {}),
        },
        "qingping": {
            "configured": bool(env.get("QINGPING_APP_KEY", "").strip()
                               and env.get("QINGPING_APP_SECRET", "").strip()),
            "app_key_hint": mask_value("QINGPING_APP_KEY",
                                       env.get("QINGPING_APP_KEY", "")),
            "app_secret_hint": mask_value("QINGPING_APP_SECRET",
                                          env.get("QINGPING_APP_SECRET", "")),
            "status": collector.status.get("qingping", {}),
        },
        "govee_iot": {
            "configured": bool(env.get("GOVEE_EMAIL", "").strip()
                               and env.get("GOVEE_PASSWORD", "").strip()),
            "email": env.get("GOVEE_EMAIL", ""),  # email is not secret
            "password_hint": mask_value("GOVEE_PASSWORD",
                                        env.get("GOVEE_PASSWORD", "")),
            "status": collector.status.get("govee_iot", {}),
        },
        "protected": bool(config.SETTINGS_TOKEN),
        "energy": {
            "peak_rate": env.get("PEAK_RATE_PER_KWH",
                                str(config.PEAK_RATE_PER_KWH)),
            "offpeak_rate": env.get("OFFPEAK_RATE_PER_KWH",
                                    str(config.OFFPEAK_RATE_PER_KWH)),
            "peak_start_hour": env.get("PEAK_START_HOUR",
                                        str(config.PEAK_START_HOUR)),
            "peak_end_hour": env.get("PEAK_END_HOUR",
                                      str(config.PEAK_END_HOUR)),
        },
        "polling": {
            "govee_poll_seconds": env.get("GOVEE_POLL_SECONDS",
                                          str(config.GOVEE_POLL_SECONDS)),
            "qingping_poll_seconds": env.get("QINGPING_POLL_SECONDS",
                                              str(config.QINGPING_POLL_SECONDS)),
            "govee_iot_poll_seconds": env.get("GOVEE_IOT_POLL_SECONDS",
                                               str(config.GOVEE_IOT_POLL_SECONDS)),
            "qingping_backfill_days": env.get("QINGPING_BACKFILL_DAYS",
                                               str(config.QINGPING_BACKFILL_DAYS)),
        },
    }


class TestRequest(BaseModel):
    integration: str
    api_key: str | None = None
    app_key: str | None = None
    app_secret: str | None = None


@app.post("/api/settings/test", dependencies=[Depends(require_settings_token)])
async def api_settings_test(req: TestRequest):
    """Validate credentials by making a lightweight API call."""
    try:
        if req.integration == "govee":
            if not req.api_key:
                raise HTTPException(400, "api_key is required")
            from .govee import GoveeClient
            client = GoveeClient(req.api_key.strip())
            devices = await client.list_devices()
            n = len(devices)
            return {"ok": True, "device_count": n,
                    "message": f"Connected — found {n} device{'s' if n != 1 else ''}"}

        elif req.integration == "qingping":
            if not req.app_key or not req.app_secret:
                raise HTTPException(400, "app_key and app_secret are required")
            from .qingping import QingpingClient
            client = QingpingClient(req.app_key.strip(), req.app_secret.strip())
            devices = await client.list_devices()
            n = len(devices)
            return {"ok": True, "device_count": n,
                    "message": f"Connected — found {n} device{'s' if n != 1 else ''}"}

        else:
            raise HTTPException(400, f"Unknown integration: {req.integration}")

    except HTTPException:
        raise
    except Exception as e:
        log.warning("Settings test failed for %s: %s", req.integration, e)
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


class SaveRequest(BaseModel):
    updates: dict[str, str]


@app.post("/api/settings/save", dependencies=[Depends(require_settings_token)])
def api_settings_save(req: SaveRequest):
    """Write settings to .env. Returns which fields changed."""
    allowed = CREDENTIAL_FIELDS | TUNING_FIELDS
    filtered = {k: v for k, v in req.updates.items() if k in allowed}
    if not filtered:
        raise HTTPException(400, "No recognised settings fields provided")

    # Validate before writing so a bad value can't land in .env
    coerced = {k: _validate_tuning(k, v) for k, v in filtered.items()
               if k in LIVE_TUNING}
    start, end = coerced.get("PEAK_START_HOUR"), coerced.get("PEAK_END_HOUR")
    if start is not None and end is not None and start == end:
        raise HTTPException(400, "Peak start and end hour cannot be the same")

    changed = write_env(filtered)

    # Apply energy settings immediately; /api/energy reads these per request.
    for key, value in coerced.items():
        if key in changed:
            setattr(config, key, value)

    needs_restart = CREDENTIAL_FIELDS | RESTART_TUNING
    return {
        "ok": True,
        "updated": changed,
        "restart_required": bool(set(changed) & needs_restart),
    }


class VerifyRequest(BaseModel):
    code: str


@app.post("/api/settings/govee-iot/verify", dependencies=[Depends(require_settings_token)])
def api_govee_iot_verify(req: VerifyRequest):
    """Complete the Govee IoT 2FA login with the emailed verification code."""
    env = read_env()
    email = env.get("GOVEE_EMAIL", "").strip()
    password = env.get("GOVEE_PASSWORD", "").strip()
    if not email or not password:
        raise HTTPException(400, "GOVEE_EMAIL and GOVEE_PASSWORD must be saved first")
    from .govee_iot import GoveeIoT, TwoFactorRequired
    client = GoveeIoT(email, password, {})
    try:
        acct = client._login(req.code.strip() if req.code.strip() else None)
        return {"ok": True,
                "message": f"Login successful — account {acct['account_id']}"}
    except TwoFactorRequired as e:
        return {"ok": False, "two_factor": True, "message": str(e)}
    except Exception as e:
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
