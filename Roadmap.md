# Roadmap

Ideas and pending work. Nothing here is committed to a schedule.

## Samsung TV energy monitoring

Goal: get the living-room TV's power draw into the same charts as the H5086
plugs, costed at the same peak/off-peak rates.

There are two ways in, and they are not equivalent.

### Option A — SmartThings API (official)

Samsung TVs from **2022 onward** report into SmartThings Energy. The REST API
exposes it through the `powerConsumptionReport` capability:

```
GET https://api.smartthings.com/v1/devices
GET https://api.smartthings.com/v1/devices/<id>/components/main/capabilities/powerConsumptionReport/status
```

Auth is a personal access token from <https://account.smartthings.com/tokens>
(scope `r:devices:*`). The device-list call also returns each device's
capabilities, which is the authoritative test for whether our specific TV
supports this — model-year rules of thumb are not reliable.

**Known problem:** `power` (instantaneous watts) is widely reported to return
`0` even on devices that display energy in the SmartThings app; the cumulative
`energy` field is the one that tends to populate. Samsung's per-device
implementations are inconsistent and TVs are less well-served than their
appliances.

**Decision gate:** run the device-list call and check whether the TV lists
`powerConsumptionReport`, then check whether `power` is non-zero. If watts come
back as 0, this option only gives coarse cumulative energy and is probably not
worth a whole new vendor integration.

Cost if we build it: new auth flow, new client module, new polling loop, new
status entry — roughly the surface area of `app/qingping.py` plus token
handling.

### Option B — put the TV on a Govee H5086 (recommended)

Zero new code. `app/govee_iot.py` already decodes watts/volts/amps from H5086
plugs, and `collector.py` picks up new devices on the next device-list refresh
(10 min). The reading lands in the same `readings` table, on the same charts,
costed automatically.

Advantages over Option A: true wall power at 60-second resolution, and it
captures **standby draw**, which is the interesting number for a TV and the one
a TV's own reporting tends to omit.

### Recommendation

Option B unless we specifically want per-app or per-input breakdowns that only
the TV knows about. Revisit Option A only if the decision-gate check above
shows non-zero watts.

## Seasonal electricity rates

The rates in `.env` are a single flat peak/off-peak pair, but PG&E E-TOU-C3 and
CleanPowerSF both price by **season** — the July 2026 bill is explicitly
labelled "Summer". PG&E summer runs Jun 1 - Sep 30; winter rates differ on both
the delivery and generation sides.

Today the app will apply summer rates to winter usage and vice versa. Options,
cheapest first:

1. Accept the error and re-run the numbers from a winter bill each October and
   June, editing `.env` by hand. Zero code.
2. Add `PEAK_RATE_PER_KWH_WINTER` / `OFFPEAK_RATE_PER_KWH_WINTER` plus a
   season check in `_rate_for()` (`6 <= month <= 9` is summer). Roughly ten
   lines, and makes historical charts correct across a season boundary — which
   option 1 cannot do, since it only ever knows today's rate.

Option 2 is worth doing before the first winter bill lands, otherwise the
autumn charts will silently mis-cost.

Reference figures from the 07/02/2026-07/30/2026 bill (summer, all-in marginal,
PG&E delivery + CleanPowerSF generation): peak $0.6061, off-peak $0.4348.
Combined bill $139.15 for 275.335 kWh = 50.54c/kWh all-in average.

---

## Pending from the Raspberry Pi migration

- **Deploy the `list_devices()` query fix.** `app/db.py` was changed to seek to
  the newest row per metric instead of running a correlated `MAX(ts)` subquery:
  `/api/devices` goes from ~22 s to ~0.1 s on the Pi (measured; identical
  output across all 6 devices). Uncommitted as of this writing.
- **Cut over from WSL2.** Both collectors ran in parallel from 2026-08-23
  11:04, so the two databases diverged. Stop the WSL2 instance, then reseed the
  Pi from a fresh snapshot to recover the gap. Qingping backfills its own last
  7 days on start; Govee readings only exist where something was polling, so
  the reseed is what preserves that window.
- **SSH from WSL2 to the Pi is still refused** — the key is present and
  `authorized_keys` permissions are correct, and the server accepts the key at
  the probe but then denies the signed request. Unresolved; worked around by
  running transfers from the Pi side instead. `sudo journalctl -u ssh` on the
  Pi would diagnose it.
