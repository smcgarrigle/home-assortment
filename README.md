# Home Sensors

Local dashboard for Govee (lights, plugs) and Qingping (air monitor, temp/RH
sensors) devices. A Python collector polls both vendors' cloud APIs into a
local SQLite database; a FastAPI app serves a chart dashboard at
<http://localhost:8088>. No Home Assistant, no external services beyond the
two vendor APIs.

## 1. Get API credentials

**Govee** — in the Govee Home app: **My Profile → Settings → Apply for API
Key**. Fill in the short form; the key arrives by email, usually within
minutes.

**Qingping** — register at <https://developer.qingping.co> (use the same
account/region as your Qingping+ app). Click your email in the top-right →
**Access Management** to find your **App Key** and **App Secret**.
Note: the API only sees devices bound to your account in the **Qingping+**
app. Wi-Fi devices (air monitors) report continuously; BLE-only temp/RH
sensors only upload when they sync through the phone app or a Qingping
gateway, so their data may lag.

## 2. Configure and run

```bash
cp .env.example .env       # then paste your keys into .env
uv venv .venv && uv pip install -r requirements.txt --python .venv/bin/python
.venv/bin/python main.py
```

Open <http://localhost:8088>. The header shows per-source status (device
counts, or the last error if a poll fails — hover the chip). Without keys the
app still runs and shows setup instructions.

To keep it running in the background:

```bash
nohup .venv/bin/python main.py >> data/server.log 2>&1 &
```

(On WSL2, add `wsl.exe -d <distro> -- <path>` to Windows Task Scheduler if you
want it to survive reboots.)

## Security

This is a **local-network tool with no authentication**. Anyone who can reach
the port can view your sensor data and, via `/setup`, see which integrations
are configured and rewrite `.env` — which means changing the credentials the
collector uses. Secrets are masked in API responses, but the write endpoints
are open by default.

That is a reasonable trade for a dashboard on a trusted home network. It is
not safe on a shared or public one.

- **Do not port-forward this, and do not put it on a public IP.** There is no
  login, no rate limiting, and no CSRF protection.
- **For remote access, use a VPN or an overlay network** — Tailscale,
  WireGuard, or similar — rather than opening a router port. This is the same
  advice Home Assistant gives, for the same reasons.
- **To restrict it to one machine**, set `HOST=127.0.0.1` in `.env`. The
  default `0.0.0.0` is what makes it reachable from your phone.
- **If others share your network**, set `SETTINGS_TOKEN` in `.env` to any
  random string. The `/setup` save and test endpoints will then require it,
  and the page prompts for it once per browser session. It does not protect
  the read-only dashboard.
- **`.env` and `data/` are gitignored** and hold your API keys, the cached
  Govee account token, and the IoT client certificate. Keep them out of any
  repo, backup, or screenshot you share.

The Govee IoT integration stores a long-lived account token in
`data/govee_account.json`, mode `600`. Treat that file as a password.

## What it does

- **Qingping**: polls the device list every 5 min (`QINGPING_POLL_SECONDS`)
  and stores every numeric reading (temperature, humidity, CO₂, PM2.5, PM10,
  TVOC, battery, …). The device itself only updates roughly every 5-10
  minutes regardless of poll rate, so polling faster doesn't add resolution
  -- it just spends more API calls landing on values that haven't changed
  yet (the readings PK discards the resulting duplicates, so no bad data
  results either way). On
  each startup it also backfills the last 7 days per device from the history
  API (`QINGPING_BACKFILL_DAYS`); duplicate timestamps are ignored, so
  restarts are safe.
- **Govee**: refreshes the device list every 10 min and polls each device's
  state every 2 min (well under the 30 req/min limits) — online, power,
  brightness, and any other numeric capability. The dashboard shows an
  on/off status indicator for each device.
- **Govee live energy (optional)**: with `GOVEE_EMAIL`/`GOVEE_PASSWORD` set,
  the app connects to Govee's private AWS IoT channel (the one the phone app
  uses — unofficial, may break) and polls watts/volts/amps from
  energy-monitoring plugs (H5086) every 60 s. First-time setup requires a 2FA
  code Govee emails you: run `python -m app.govee_login` to trigger the email,
  then `python -m app.govee_login <code>` to cache the account token
  (`data/govee_account.json`). The official developer API does not expose this
  data at all.
- **Energy cost**: the energy chart's kWh totals are also costed out at
  peak/off-peak electricity rates (`PEAK_RATE_PER_KWH`, `OFFPEAK_RATE_PER_KWH`
  in `.env`). The peak window is `PEAK_START_HOUR`–`PEAK_END_HOUR` (default
  4pm–9pm local time) **every day, weekends included**, matching PG&E
  E-TOU-C3; everything else is off-peak. Cost is computed per hour and rolled up, so a daily or
  weekly bucket that spans both rates still totals correctly.
- **Storage**: SQLite at `data/sensors.db`, one row per
  (device, metric, timestamp). Chart queries bucket-average to ≤ ~600 points.
- **Dashboard layout**: device cards, then a **Power draw** chart (every
  plug overlaid on one chart), then **Energy usage (by plug)** — all
  devices, then broken down by one. An **Environmental information**
  divider follows, then the Qingping charts (temperature, humidity, CO₂,
  PM2.5, PM10, TVOC, pressure). Metrics that are just a live value on a
  device card — brightness, on/off — aren't charted separately. The
  24h/7d/30d/90d range picker applies to every chart at once.

## Layout

| Path | Purpose |
|---|---|
| `app/govee.py`, `app/qingping.py` | API clients (official) |
| `app/govee_iot.py`, `app/govee_login.py` | unofficial Govee IoT channel for plug energy data |
| `app/collector.py` | background polling loops |
| `app/db.py` | SQLite schema and queries |
| `app/web.py` | FastAPI routes (`/api/devices`, `/api/history`, `/api/energy`, `/api/status`, `/setup`) |
| `app/static/` | dashboard (vendored Chart.js, no CDN) |

## API endpoints used

- Govee: `https://openapi.api.govee.com/router/api/v1/…` with the
  `Govee-API-Key` header (`user/devices`, `device/state`).
- Qingping: OAuth2 client-credentials token from
  `https://oauth.cleargrass.com/oauth2/token`, then
  `https://apis.cleargrass.com/v1/apis/devices` (latest data) and
  `…/devices/data` (history; `start_time`/`end_time` in seconds, `timestamp`
  in ms).
