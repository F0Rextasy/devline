"""SQLite timeline store — one file, zero migrations beyond CREATE IF NOT EXISTS."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('shell', 'editor', 'agent')),
  command TEXT NOT NULL,
  cwd TEXT NOT NULL DEFAULT '',
  exit_code INTEGER,
  duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
"""

DEDUPE_WINDOW_SEC = 3.0


def db_path() -> Path:
    override = os.environ.get("DEVLINE_DB")
    if override:
        return Path(override)
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / ".devline" / "timeline.sqlite"


def open_db(path: Path | None = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_event(
    conn: sqlite3.Connection,
    *,
    ts: str,
    source: str,
    command: str,
    cwd: str = "",
    exit_code: int | None = None,
    duration_ms: int | None = None,
) -> bool:
    """Insert one event; returns False when dropped as an immediate duplicate."""
    last = conn.execute(
        "SELECT ts, source, command, cwd FROM events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if last is not None and last["source"] == source and last["command"] == command and last["cwd"] == cwd:
        try:
            from datetime import datetime

            delta = abs(
                (datetime.fromisoformat(ts) - datetime.fromisoformat(last["ts"])).total_seconds()
            )
            if delta <= DEDUPE_WINDOW_SEC:
                return False
        except ValueError:
            pass
    conn.execute(
        "INSERT INTO events (ts, source, command, cwd, exit_code, duration_ms)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (ts, source, command, cwd, exit_code, duration_ms),
    )
    conn.commit()
    return True
