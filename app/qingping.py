"""Client for the Qingping open API (https://developer.qingping.co)."""
import base64
import time

import httpx

OAUTH_URL = "https://oauth.cleargrass.com/oauth2/token"
BASE = "https://apis.cleargrass.com"


class QingpingClient:
    def __init__(self, app_key: str, app_secret: str):
        self._basic = base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()
        self._token: str | None = None
        self._token_expiry = 0.0
        self._client = httpx.AsyncClient(base_url=BASE, timeout=20)

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                OAUTH_URL,
                headers={"Authorization": f"Basic {self._basic}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials",
                      "scope": "device_full_access"},
            )
        r.raise_for_status()
        body = r.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 7200))
        return self._token

    async def _get(self, path: str, params: dict) -> dict:
        token = await self._get_token()
        params = {**params, "timestamp": int(time.time() * 1000)}
        r = await self._client.get(
            path, params=params, headers={"Authorization": f"Bearer {token}"}
        )
        r.raise_for_status()
        return r.json()

    async def list_devices(self) -> list[dict]:
        body = await self._get("/v1/apis/devices", {})
        return body.get("devices", [])

    async def device_history(self, mac: str, start_ts: int, end_ts: int,
                             limit: int = 200, offset: int = 0) -> dict:
        return await self._get(
            "/v1/apis/devices/data",
            {"mac": mac, "start_time": start_ts, "end_time": end_ts,
             "limit": limit, "offset": offset},
        )

    @staticmethod
    def numeric_data(data: dict) -> tuple[int | None, dict[str, float]]:
        """A Qingping data blob is {metric: {value: x}, ...}. Returns (ts, metrics)."""
        ts = None
        out: dict[str, float] = {}
        for key, entry in (data or {}).items():
            if not isinstance(entry, dict) or "value" not in entry:
                continue
            value = entry["value"]
            if key == "timestamp":
                ts = int(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                out[key] = float(value)
        return ts, out
