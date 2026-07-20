"""Live power readings for Govee plugs via the app's private AWS IoT channel.

This is the reverse-engineered path the Govee Home app uses, ported from
govee2mqtt (login/IoT-key flow) and homebridge-govee (aa19 power decode).
It is unofficial and may break if Govee changes their backend.
"""
import base64
import json
import logging
import threading
import time
import uuid

import certifi
import httpx
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, pkcs12,
)
from paho.mqtt import client as mqtt

from . import config, db

log = logging.getLogger("govee_iot")

APP_VERSION = "7.4.10"
USER_AGENT = (
    f"GoveeHome/{APP_VERSION} (com.ihoment.GoVeeSensor; build:8; iOS 26.5.0)"
    " Alamofire/5.11.0"
)
# v2 login: Govee added email 2FA mid-2026; status 454 = code required,
# 455 = code wrong/expired (govee2mqtt #682, homebridge-govee http.js)
LOGIN_URL = "https://app2.govee.com/account/rest/account/v2/login"
VERIFY_URL = "https://app2.govee.com/account/rest/account/v1/verification"
IOT_KEY_URL = "https://app2.govee.com/app/v1/account/iot/key"
DEVICE_LIST_URL = "https://app2.govee.com/device/rest/devices/v1/list"


class TwoFactorRequired(Exception):
    """Login needs the emailed verification code; do not retry automatically."""


def decode_power(raw: bytes):
    """aa19 packet: volts@8-9, amps@10-11, watts@12-14, all /100."""
    if len(raw) < 15 or raw[0] != 0xAA or raw[1] != 0x19:
        return None
    return {
        "voltage": int.from_bytes(raw[8:10], "big") / 100,
        "current": int.from_bytes(raw[10:12], "big") / 100,
        "power": int.from_bytes(raw[12:15], "big") / 100,
    }


class GoveeIoT:
    def __init__(self, email: str, password: str, status: dict):
        self.email = email
        self.password = password
        self.status = status
        self.client_id = uuid.uuid5(uuid.NAMESPACE_DNS, email).hex
        self.account: dict = {}
        self.topics: dict[str, dict] = {}  # device mac -> {topic, sku, name}
        self.mqtt_client: mqtt.Client | None = None
        self._cache_path = config.DB_PATH.parent / "govee_account.json"

    def _headers(self, token: str | None = None) -> dict:
        h = {
            "appVersion": APP_VERSION,
            "clientId": self.client_id,
            "clientType": "1",
            "iotVersion": "0",
            "timestamp": str(int(time.time() * 1000)),
            "User-Agent": USER_AGENT,
        }
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _login(self, code: str | None = None) -> dict:
        if self._cache_path.exists():
            cached = json.loads(self._cache_path.read_text())
            if cached.get("email") == self.email and time.time() < cached.get("expires_at", 0):
                return cached
        payload = {"email": self.email, "password": self.password,
                   "client": self.client_id}
        if code:
            payload["code"] = code
        r = httpx.post(LOGIN_URL, headers=self._headers(), json=payload, timeout=30)
        r.raise_for_status()
        body = r.json()
        if body.get("status") == 454:
            if code:
                raise TwoFactorRequired("2FA code was rejected as invalid")
            # ask Govee to email a verification code, then bail out —
            # retrying would spam a fresh code each attempt
            httpx.post(VERIFY_URL, headers=self._headers(),
                       json={"type": 8, "email": self.email}, timeout=30)
            raise TwoFactorRequired(
                "Govee emailed you a verification code; complete login with "
                "`python -m app.govee_login <code>` then restart"
            )
        if body.get("status") == 455:
            raise TwoFactorRequired("2FA code wrong or expired; request a new one")
        if body.get("status") != 200 or "client" not in body:
            raise RuntimeError(f"Govee app login failed: {body.get('message')}")
        c = body["client"]
        acct = {
            "email": self.email,
            "token": c["token"],
            "account_id": c["accountId"],
            "topic": c["topic"],
            "expires_at": time.time() + int(c.get("tokenExpireCycle", 86400)) - 3600,
        }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(acct))
        self._cache_path.chmod(0o600)
        return acct

    def _fetch_iot_certs(self, token: str) -> tuple[str, str]:
        """Returns (endpoint, cert_dir) after writing PEM cert/key files."""
        r = httpx.get(IOT_KEY_URL, headers=self._headers(token), timeout=30)
        r.raise_for_status()
        data = r.json()["data"]
        p12_bytes = base64.b64decode(data["p12"])
        p12_pass = data.get("p12Pass") or data.get("p12_pass") or ""
        key, cert, _extra = pkcs12.load_key_and_certificates(
            p12_bytes, p12_pass.encode()
        )
        cert_dir = config.DB_PATH.parent
        cert_path = cert_dir / "govee_iot_cert.pem"
        key_path = cert_dir / "govee_iot_key.pem"
        cert_path.write_bytes(cert.public_bytes(Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        )
        key_path.chmod(0o600)
        return data["endpoint"], str(cert_dir)

    def _refresh_devices(self, token: str):
        r = httpx.post(DEVICE_LIST_URL, headers=self._headers(token), timeout=30)
        r.raise_for_status()
        for entry in r.json().get("devices", []):
            try:
                settings = json.loads(
                    entry.get("deviceExt", {}).get("deviceSettings", "{}")
                )
            except (TypeError, json.JSONDecodeError):
                continue
            topic = settings.get("topic")
            if topic:
                self.topics[entry["device"]] = {
                    "topic": topic,
                    "sku": entry.get("sku"),
                    "name": entry.get("deviceName"),
                }
        log.info("IoT: %d devices with topics", len(self.topics))

    def _store(self, device_mac: str, sku: str | None, metrics: dict):
        row = db.connect().execute(
            "SELECT id FROM devices WHERE source='govee' AND external_id=?",
            (device_mac,),
        ).fetchone()
        if row:
            dev_id = row["id"]
        else:
            info = self.topics.get(device_mac, {})
            dev_id = db.upsert_device(
                "govee", device_mac, sku or info.get("sku"),
                info.get("name") or device_mac, {},
            )
        ts = int(time.time())
        db.insert_readings([(dev_id, m, ts, v) for m, v in metrics.items()])

    def _on_message(self, _client, _userdata, msg):
        try:
            packet = json.loads(msg.payload)
            if isinstance(packet.get("msg"), str):
                try:
                    packet.update(json.loads(packet["msg"]))
                except json.JSONDecodeError:
                    pass
            state = packet.get("state") or {}
            device_mac = packet.get("device") or state.get("device")
            sku = packet.get("sku") or state.get("sku")
            if not device_mac:
                return
            metrics: dict[str, float] = {}
            if state.get("onOff") is not None:
                metrics["powerSwitch"] = float(state["onOff"] != 0)
            for cmd in (packet.get("op") or {}).get("command", []):
                try:
                    power = decode_power(base64.b64decode(cmd))
                except (ValueError, TypeError):
                    continue
                if power:
                    metrics.update(power)
            if metrics:
                self._store(device_mac, sku, metrics)
                self.status["last_success"] = int(time.time())
                self.status["last_error"] = None
        except Exception as e:
            log.exception("IoT message handling failed")
            self.status["last_error"] = f"{type(e).__name__}: {e}"

    def _request_status_loop(self):
        while True:
            client = self.mqtt_client
            if client and client.is_connected():
                for mac, info in list(self.topics.items()):
                    payload = json.dumps({
                        "msg": {
                            "cmd": "status",
                            "cmdVersion": 2,
                            "transaction": f"v_{int(time.time() * 1000)}000",
                            "type": 0,
                        }
                    })
                    client.publish(info["topic"], payload, qos=0)
            time.sleep(config.GOVEE_IOT_POLL_SECONDS)

    def _run(self):
        backoff = 30
        while True:
            try:
                self.account = self._login()
                token = self.account["token"]
                endpoint, cert_dir = self._fetch_iot_certs(token)
                self._refresh_devices(token)

                client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION2,
                    client_id=f"AP/{self.account['account_id']}/{uuid.uuid4().hex}",
                    protocol=mqtt.MQTTv311,
                )
                client.tls_set(
                    ca_certs=certifi.where(),
                    certfile=f"{cert_dir}/govee_iot_cert.pem",
                    keyfile=f"{cert_dir}/govee_iot_key.pem",
                )
                account_topic = self.account["topic"]

                def on_connect(c, _u, _f, reason_code, _p=None):
                    log.info("IoT connected: %s", reason_code)
                    self.status["connected"] = True
                    c.subscribe(account_topic, qos=0)

                def on_disconnect(_c, _u, _f, reason_code, _p=None):
                    log.warning("IoT disconnected: %s", reason_code)
                    self.status["connected"] = False

                client.on_connect = on_connect
                client.on_disconnect = on_disconnect
                client.on_message = self._on_message
                client.connect(endpoint, 8883, keepalive=120)
                self.mqtt_client = client
                backoff = 30
                client.loop_forever(retry_first_connection=False)
            except TwoFactorRequired as e:
                log.error("IoT login needs 2FA: %s", e)
                self.status["last_error"] = str(e)
                self.status["connected"] = False
                return  # do not retry: each retry would trigger a new email
            except Exception as e:
                log.exception("IoT client failed; retrying in %ss", backoff)
                self.status["last_error"] = f"{type(e).__name__}: {e}"
                self.status["connected"] = False
                time.sleep(backoff)
                backoff = min(backoff * 2, 600)

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="govee-iot").start()
        threading.Thread(target=self._request_status_loop, daemon=True,
                         name="govee-iot-poll").start()
