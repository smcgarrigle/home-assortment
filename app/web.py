import asyncio
import contextlib
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


from . import collector, config, db

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
    is_peak = (d.weekday() < 5
               and config.PEAK_START_HOUR <= d.hour < config.PEAK_END_HOUR)
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


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
