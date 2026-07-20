"""Background polling loops that pull cloud data into SQLite."""
import asyncio
import logging
import time

from . import config, db
from .govee import GoveeClient
from .qingping import QingpingClient

log = logging.getLogger("collector")

# Shared status surfaced at /api/status
status: dict = {
    "govee": {"configured": bool(config.GOVEE_API_KEY),
              "last_success": None, "last_error": None, "device_count": 0},
    "qingping": {"configured": bool(config.QINGPING_APP_KEY and config.QINGPING_APP_SECRET),
                 "last_success": None, "last_error": None, "device_count": 0},
    "govee_iot": {"configured": bool(config.GOVEE_EMAIL and config.GOVEE_PASSWORD),
                  "connected": False, "last_success": None, "last_error": None},
}

govee_client: GoveeClient | None = None
_govee_devices: dict[int, dict] = {}  # db id -> {sku, device}


def govee_device_info(device_id: int) -> dict | None:
    return _govee_devices.get(device_id)


async def _qingping_backfill(client: QingpingClient, dev_id: int, mac: str):
    """Fetch history for the configured window, walking 48h slices."""
    end = int(time.time())
    start = end - config.QINGPING_BACKFILL_DAYS * 86400
    cursor = start
    total = 0
    while cursor < end:
        slice_end = min(cursor + 48 * 3600, end)
        offset = 0
        while True:
            body = await client.device_history(mac, cursor, slice_end, offset=offset)
            entries = body.get("data", [])
            if not entries:
                break
            rows = []
            for entry in entries:
                ts, metrics = QingpingClient.numeric_data(entry)
                if ts:
                    rows.extend((dev_id, m, ts, v) for m, v in metrics.items())
            total += db.insert_readings(rows)
            if len(entries) < 200:
                break
            offset += len(entries)
            await asyncio.sleep(1)
        cursor = slice_end
    log.info("qingping backfill %s: %d new readings", mac, total)


async def qingping_loop():
    st = status["qingping"]
    if not st["configured"]:
        return
    client = QingpingClient(config.QINGPING_APP_KEY, config.QINGPING_APP_SECRET)
    backfilled: set[int] = set()
    while True:
        try:
            devices = await client.list_devices()
            st["device_count"] = len(devices)
            for entry in devices:
                info = entry.get("info", {})
                mac = info.get("mac")
                if not mac:
                    continue
                product = info.get("product", {})
                dev_id = db.upsert_device(
                    "qingping", mac,
                    product.get("en_name") or product.get("name"),
                    info.get("name") or mac,
                    {"product": product},
                )
                ts, metrics = QingpingClient.numeric_data(entry.get("data", {}))
                if ts and metrics:
                    db.insert_readings([(dev_id, m, ts, v) for m, v in metrics.items()])
                if dev_id not in backfilled:
                    # once per process: fills gaps since last run; PK dedupes overlaps
                    backfilled.add(dev_id)
                    try:
                        await _qingping_backfill(client, dev_id, mac)
                    except Exception:
                        log.exception("backfill failed for %s", mac)
            st["last_success"] = int(time.time())
            st["last_error"] = None
        except Exception as e:
            st["last_error"] = f"{type(e).__name__}: {e}"
            log.exception("qingping poll failed")
        await asyncio.sleep(config.QINGPING_POLL_SECONDS)


async def govee_loop():
    global govee_client
    st = status["govee"]
    if not st["configured"]:
        return
    govee_client = GoveeClient(config.GOVEE_API_KEY)
    device_list: list[dict] = []
    refresh_at = 0.0
    while True:
        try:
            now = time.time()
            if now >= refresh_at:
                device_list = await govee_client.list_devices()
                st["device_count"] = len(device_list)
                refresh_at = now + 600
                for d in device_list:
                    dev_id = db.upsert_device(
                        "govee", d["device"], d.get("sku"),
                        d.get("deviceName") or d.get("sku"),
                        {"type": d.get("type"),
                         "capabilities": [c.get("instance") for c in d.get("capabilities", [])]},
                    )
                    _govee_devices[dev_id] = {"sku": d.get("sku"), "device": d["device"]}
            ts = int(time.time())
            for dev_id, ident in list(_govee_devices.items()):
                caps = await govee_client.get_state(ident["sku"], ident["device"])
                metrics = GoveeClient.numeric_state(caps)
                db.insert_readings([(dev_id, m, ts, v) for m, v in metrics.items()])
                await asyncio.sleep(0.5)
            st["last_success"] = int(time.time())
            st["last_error"] = None
        except Exception as e:
            st["last_error"] = f"{type(e).__name__}: {e}"
            log.exception("govee poll failed")
        await asyncio.sleep(config.GOVEE_POLL_SECONDS)


def start(loop: asyncio.AbstractEventLoop) -> list[asyncio.Task]:
    if status["govee_iot"]["configured"]:
        from .govee_iot import GoveeIoT
        GoveeIoT(config.GOVEE_EMAIL, config.GOVEE_PASSWORD,
                 status["govee_iot"]).start()
    return [loop.create_task(qingping_loop()), loop.create_task(govee_loop())]
