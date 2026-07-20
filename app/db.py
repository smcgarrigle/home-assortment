import json
import sqlite3
import threading

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    model TEXT,
    name TEXT,
    meta TEXT,
    UNIQUE(source, external_id)
);
CREATE TABLE IF NOT EXISTS readings (
    device_id INTEGER NOT NULL REFERENCES devices(id),
    metric TEXT NOT NULL,
    ts INTEGER NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (device_id, metric, ts)
) WITHOUT ROWID;
"""


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


def upsert_device(source: str, external_id: str, model: str | None,
                  name: str | None, meta: dict | None = None) -> int:
    conn = connect()
    with _lock:
        conn.execute(
            """INSERT INTO devices(source, external_id, model, name, meta)
               VALUES(?,?,?,?,?)
               ON CONFLICT(source, external_id) DO UPDATE SET
                 model=excluded.model, name=excluded.name, meta=excluded.meta""",
            (source, external_id, model, name, json.dumps(meta or {})),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM devices WHERE source=? AND external_id=?",
            (source, external_id),
        ).fetchone()
    return row["id"]


def insert_readings(rows: list[tuple[int, str, int, float]]) -> int:
    """rows: (device_id, metric, ts, value). Returns count of new rows."""
    if not rows:
        return 0
    conn = connect()
    with _lock:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO readings(device_id, metric, ts, value) VALUES(?,?,?,?)",
            rows,
        )
        conn.commit()
        return conn.total_changes - before


def list_devices() -> list[dict]:
    conn = connect()
    with _lock:
        devs = [dict(r) for r in conn.execute("SELECT * FROM devices ORDER BY id")]
        for d in devs:
            d["meta"] = json.loads(d["meta"] or "{}")
            latest = conn.execute(
                """SELECT metric, ts, value FROM readings r
                   WHERE device_id=? AND ts=(SELECT MAX(ts) FROM readings
                       WHERE device_id=r.device_id AND metric=r.metric)""",
                (d["id"],),
            ).fetchall()
            d["latest"] = {r["metric"]: {"ts": r["ts"], "value": r["value"]} for r in latest}
    return devs


def history(device_id: int, metric: str, since_ts: int, bucket: int) -> list[list]:
    conn = connect()
    with _lock:
        rows = conn.execute(
            """SELECT (ts / ?) * ? AS t, AVG(value) AS v FROM readings
               WHERE device_id=? AND metric=? AND ts>=?
               GROUP BY t ORDER BY t""",
            (bucket, bucket, device_id, metric, since_ts),
        ).fetchall()
    return [[r["t"], round(r["v"], 2)] for r in rows]


def series(device_id: int, metric: str, since_ts: int) -> list[tuple[int, float]]:
    conn = connect()
    with _lock:
        rows = conn.execute(
            "SELECT ts, value FROM readings WHERE device_id=? AND metric=? AND ts>=? ORDER BY ts",
            (device_id, metric, since_ts),
        ).fetchall()
    return [(r["ts"], r["value"]) for r in rows]


def earliest_ts(device_id: int) -> int | None:
    conn = connect()
    with _lock:
        row = conn.execute(
            "SELECT MIN(ts) AS t FROM readings WHERE device_id=?", (device_id,)
        ).fetchone()
    return row["t"]
