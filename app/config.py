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
