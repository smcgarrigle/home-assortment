import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

GOVEE_API_KEY = os.getenv("GOVEE_API_KEY", "").strip()
GOVEE_EMAIL = os.getenv("GOVEE_EMAIL", "").strip()
GOVEE_PASSWORD = os.getenv("GOVEE_PASSWORD", "").strip()
GOVEE_IOT_POLL_SECONDS = int(os.getenv("GOVEE_IOT_POLL_SECONDS", "60"))
QINGPING_APP_KEY = os.getenv("QINGPING_APP_KEY", "").strip()
QINGPING_APP_SECRET = os.getenv("QINGPING_APP_SECRET", "").strip()

DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "sensors.db")))
PORT = int(os.getenv("PORT", "8088"))

GOVEE_POLL_SECONDS = int(os.getenv("GOVEE_POLL_SECONDS", "120"))
QINGPING_POLL_SECONDS = int(os.getenv("QINGPING_POLL_SECONDS", "60"))
QINGPING_BACKFILL_DAYS = int(os.getenv("QINGPING_BACKFILL_DAYS", "7"))

# Electricity rates used to cost out energy_kwh readings. Peak window is
# PEAK_START_HOUR..PEAK_END_HOUR (local time) every day, weekends included,
# matching PG&E E-TOU-C3; everything else is off-peak.
PEAK_RATE_PER_KWH = float(os.getenv("PEAK_RATE_PER_KWH", "0.6061"))
OFFPEAK_RATE_PER_KWH = float(os.getenv("OFFPEAK_RATE_PER_KWH", "0.4348"))
PEAK_START_HOUR = int(os.getenv("PEAK_START_HOUR", "16"))
PEAK_END_HOUR = int(os.getenv("PEAK_END_HOUR", "21"))
