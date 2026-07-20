"""Import Govee app CSV exports (plug electricity history) into the readings table.

The export format isn't documented, so the parser sniffs the header row and
maps columns by name. Duplicate (device, metric, timestamp) rows are ignored,
so re-importing overlapping exports is safe.
"""
import csv
import io
import re
from datetime import datetime

from . import db

TIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d", "%Y/%m/%d",
]

# header-name fragment -> stored metric (order matters: first match wins)
COLUMN_RULES = [
    (r"kwh|energy|consumption", "energy_kwh"),
    (r"power|watt|\(w\)", "power"),
    (r"current|ampere|\(a\)", "current"),
    (r"voltage|volt|\(v\)", "voltage"),
]
TIME_RULE = re.compile(r"time|date", re.I)


def _parse_ts(cell: str) -> int | None:
    cell = cell.strip().strip('"')
    if re.fullmatch(r"\d{10}", cell):
        return int(cell)
    if re.fullmatch(r"\d{13}", cell):
        return int(cell) // 1000
    for fmt in TIME_FORMATS:
        try:
            return int(datetime.strptime(cell, fmt).timestamp())
        except ValueError:
            continue
    return None


def _parse_value(cell: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", cell.replace(",", ""))
    return float(m.group()) if m else None


def sniff(text: str) -> dict:
    """Locate the header row and map columns. Returns {header_idx, time_col, metrics: {col: metric}, delimiter}."""
    sample = text[:4096]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    for i, row in enumerate(rows[:10]):
        cols = [c.strip().lower() for c in row]
        time_col = next((j for j, c in enumerate(cols) if TIME_RULE.search(c)), None)
        if time_col is None:
            continue
        metrics = {}
        for j, c in enumerate(cols):
            if j == time_col:
                continue
            for pattern, metric in COLUMN_RULES:
                if re.search(pattern, c):
                    metrics[j] = metric
                    break
        if metrics:
            return {"header_idx": i, "time_col": time_col,
                    "metrics": metrics, "delimiter": delimiter, "rows": rows}
    raise ValueError(
        "Could not find a header row with a time column and at least one "
        "power/energy/current/voltage column"
    )


def import_csv(text: str, device_id: int) -> dict:
    layout = sniff(text)
    to_insert = []
    skipped = 0
    times = []
    for row in layout["rows"][layout["header_idx"] + 1:]:
        if len(row) <= layout["time_col"]:
            continue
        ts = _parse_ts(row[layout["time_col"]])
        if ts is None:
            skipped += 1
            continue
        times.append(ts)
        for col, metric in layout["metrics"].items():
            if col < len(row):
                value = _parse_value(row[col])
                if value is not None:
                    to_insert.append((device_id, metric, ts, value))
    inserted = db.insert_readings(to_insert)
    return {
        "rows": len(times),
        "skipped_rows": skipped,
        "readings_parsed": len(to_insert),
        "readings_new": inserted,
        "metrics": sorted(set(layout["metrics"].values())),
        "from": min(times) if times else None,
        "to": max(times) if times else None,
    }
