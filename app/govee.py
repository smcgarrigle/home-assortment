"""Client for the Govee developer cloud API (https://developer.govee.com)."""
import uuid

import httpx

BASE = "https://openapi.api.govee.com"


class GoveeClient:
    def __init__(self, api_key: str):
        self._client = httpx.AsyncClient(
            base_url=BASE,
            headers={"Govee-API-Key": api_key, "Content-Type": "application/json"},
            timeout=20,
        )

    async def list_devices(self) -> list[dict]:
        r = await self._client.get("/router/api/v1/user/devices")
        r.raise_for_status()
        body = r.json()
        data = body.get("data", [])
        if isinstance(data, dict):
            data = data.get("devices", [])
        return data

    async def get_state(self, sku: str, device: str) -> list[dict]:
        """Returns the capability list with current state values."""
        r = await self._client.post(
            "/router/api/v1/device/state",
            json={"requestId": str(uuid.uuid4()),
                  "payload": {"sku": sku, "device": device}},
        )
        r.raise_for_status()
        return r.json().get("payload", {}).get("capabilities", [])

    async def control(self, sku: str, device: str, capability_type: str,
                      instance: str, value) -> dict:
        r = await self._client.post(
            "/router/api/v1/device/control",
            json={"requestId": str(uuid.uuid4()),
                  "payload": {"sku": sku, "device": device,
                              "capability": {"type": capability_type,
                                             "instance": instance,
                                             "value": value}}},
        )
        r.raise_for_status()
        return r.json()

    @staticmethod
    def numeric_state(capabilities: list[dict]) -> dict[str, float]:
        """Flatten capability states to numeric metrics keyed by instance name."""
        out: dict[str, float] = {}
        for cap in capabilities:
            instance = cap.get("instance")
            state = cap.get("state") or {}
            value = state.get("value")
            if instance is None:
                continue
            if isinstance(value, bool):
                out[instance] = 1.0 if value else 0.0
            elif isinstance(value, (int, float)):
                out[instance] = float(value)
        return out
