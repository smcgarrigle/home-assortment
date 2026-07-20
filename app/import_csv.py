"""CLI for importing a Govee CSV export.

Usage:
  python -m app.import_csv <file.csv> <device-name-or-id>
  python -m app.import_csv --list          # show device ids/names
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from . import db
from .importer import import_csv

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--list":
        for d in db.list_devices():
            print(f"[{d['id']}] {d['source']}: {d['name']}")
        sys.exit(0)
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    path, ident = Path(sys.argv[1]), sys.argv[2]
    devices = db.list_devices()
    match = [d for d in devices
             if str(d["id"]) == ident or ident.lower() in (d["name"] or "").lower()]
    if len(match) != 1:
        print(f"Device {ident!r} matched {len(match)} devices; use --list")
        sys.exit(1)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    result = import_csv(text, match[0]["id"])
    result["device"] = match[0]["name"]
    for k in ("from", "to"):
        if result[k]:
            result[k] = datetime.fromtimestamp(result[k]).strftime("%Y-%m-%d %H:%M")
    print(json.dumps(result, indent=1))
