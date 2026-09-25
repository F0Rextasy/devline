"""Day reports: `devline friday` and explicit --date queries."""
from __future__ import annotations

import datetime as dt
import sqlite3
from collections import OrderedDict

DAY_START = "T00:00:00"
DAY_END = "T23:59:59.999999"


def friday_of(today: dt.date) -> dt.date:
    """The most recent Friday, inclusive of `today`."""
    return today - dt.timedelta(days=(today.weekday() - 4) % 7)


def day_window(day: dt.date) -> tuple[str, str]:
    start = day.isoformat() + DAY_START
    end = day.isoformat() + DAY_END
    return start, end


def query_day(conn: sqlite3.Connection, day: dt.date) -> list[sqlite3.Row]:
    start, end = day_window(day)
    return conn.execute(
        "SELECT ts, source, command, cwd, exit_code, duration_ms FROM events"
        " WHERE ts BETWEEN ? AND ? ORDER BY ts, id",
        (start, end),
    ).fetchall()


def project_of(cwd: str) -> str:
    parts = [p for p in cwd.replace("\\", "/").split("/") if p]
    return parts[-1] if parts else "(no cwd)"


def summarize(rows: list[sqlite3.Row]) -> dict:
    projects: "OrderedDict[str, dict]" = OrderedDict()
    tracked_ms = 0
    for r in rows:
        proj = project_of(r["cwd"])
        bucket = projects.setdefault(proj, {"project": proj, "events": 0, "duration_ms": 0})
        bucket["events"] += 1
        if r["duration_ms"]:
            bucket["duration_ms"] += r["duration_ms"]
            tracked_ms += r["duration_ms"]
    ordered = sorted(projects.values(), key=lambda p: (-p["events"], p["project"]))
    sources: "OrderedDict[str, int]" = OrderedDict()
    for r in rows:
        sources[r["source"]] = sources.get(r["source"], 0) + 1
    return {
        "events": len(rows),
        "projects": len(ordered),
        "tracked_ms": tracked_ms,
        "by_project": ordered,
        "by_source": dict(sources),
    }


def format_day(day: dt.date, rows: list[sqlite3.Row]) -> str:
    label = day.strftime("%A %Y-%m-%d")
    if not rows:
        return (
            f"no events on {label}\n"
            "nothing recorded — install the hooks first: devline install-hook"
        )
    stats = summarize(rows)
    tracked = _duration(stats["tracked_ms"]) if stats["tracked_ms"] else "0s"
    events = "event" if stats["events"] == 1 else "events"
    projects = "project" if stats["projects"] == 1 else "projects"
    lines = [
        f"{label} — {stats['events']} {events} · {stats['projects']} {projects} · {tracked} tracked",
        "",
    ]
    for r in rows:
        time = r["ts"][11:19] if len(r["ts"]) >= 19 else r["ts"]
        proj = project_of(r["cwd"])[:16].ljust(16)
        exit_part = ""
        if r["exit_code"] is not None:
            exit_part = f" (exit {r['exit_code']})"
        dur = f" · {_duration(r['duration_ms'])}" if r["duration_ms"] else ""
        cmd = r["command"] if len(r["command"]) <= 70 else r["command"][:67] + "..."
        lines.append(f"{time}  {r['source']:<6} {proj} {cmd}{exit_part}{dur}")
    lines.append("")
    lines.append("by project:")
    width = max(len(p["project"]) for p in stats["by_project"])
    for p in stats["by_project"]:
        d = f" · {_duration(p['duration_ms'])}" if p["duration_ms"] else ""
        lines.append(f"  {p['project']:<{width}}  {p['events']} events{d}")
    return "\n".join(lines)


def _duration(ms: int) -> str:
    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def day_payload(day: dt.date, rows: list[sqlite3.Row]) -> dict:
    stats = summarize(rows) if rows else {
        "events": 0, "projects": 0, "tracked_ms": 0, "by_project": [], "by_source": {},
    }
    return {
        "date": day.isoformat(),
        "label": day.strftime("%A %Y-%m-%d"),
        "summary": stats,
        "events": [
            {
                "ts": r["ts"],
                "source": r["source"],
                "command": r["command"],
                "cwd": r["cwd"],
                "exit_code": r["exit_code"],
                "duration_ms": r["duration_ms"],
            }
            for r in rows
        ],
    }
