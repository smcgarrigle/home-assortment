import asyncio
import httpx
import logging
import time
from . import db, config

log = logging.getLogger("pirate_weather")

async def get_lat_lon(zip_code):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"https://api.zippopotam.us/us/{zip_code}")
        r.raise_for_status()
        data = r.json()
        place = data["places"][0]
        return place["latitude"], place["longitude"]

async def poll_loop():
    if not config.PIRATE_WEATHER_API_KEY or not config.WEATHER_ZIP_CODE:
        log.info("Pirate Weather disabled: missing API key or ZIP code")
        return

    try:
        lat, lon = await get_lat_lon(config.WEATHER_ZIP_CODE)
        log.info(f"Resolved ZIP {config.WEATHER_ZIP_CODE} to {lat}, {lon}")
    except Exception as e:
        log.error(f"Failed to geocode zip code {config.WEATHER_ZIP_CODE}: {e}")
        return

    device_id = db.upsert_device(
        "pirate_weather",
        f"zip-{config.WEATHER_ZIP_CODE}",
        "Pirate Weather API",
        f"Outdoor ({config.WEATHER_ZIP_CODE})",
        {"type": "devices.types.weather"}
    )

    url = f"https://api.pirateweather.net/forecast/{config.PIRATE_WEATHER_API_KEY}/{lat},{lon}?units=si"
    
    while True:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=10.0)
                r.raise_for_status()
                data = r.json()
                currently = data.get("currently", {})
                
                metrics = {}
                if "temperature" in currently:
                    metrics["temperature"] = currently["temperature"]
                if "humidity" in currently:
                    metrics["humidity"] = currently["humidity"] * 100
                if "pressure" in currently:
                    metrics["pressure"] = currently["pressure"]
                if "windSpeed" in currently:
                    metrics["windSpeed"] = currently["windSpeed"]
                if "dewPoint" in currently:
                    metrics["dewPoint"] = currently["dewPoint"]
                
                ts = int(currently.get("time", time.time()))
                
                rows = []
                for m, v in metrics.items():
                    rows.append((device_id, m, ts, v))
                
                db.insert_readings(rows)
                
                from .collector import status
                status["pirate_weather"] = {"connected": True, "last_success": int(time.time()), "configured": True}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"Failed to fetch pirate weather: {e}")
            from .collector import status
            if "pirate_weather" not in status:
                status["pirate_weather"] = {}
            status["pirate_weather"].update({"connected": False, "last_error": str(e), "configured": True})

        await asyncio.sleep(config.WEATHER_POLL_SECONDS)
