"""devline CLI — install-hook, record, friday."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from . import __version__
from .db import insert_event, open_db
from .hooks import install_agent, install_editor, install_git_hook, install_shell
from .report import format_day, day_payload, friday_of, query_day

SOURCES = ("shell", "editor", "agent")


def _now_iso() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def cmd_install_hook(args: argparse.Namespace) -> int:
    what = args.target
    dry = args.dry_run
    results: dict[str, str] = {}
    if what in ("shell", "all"):
        results.update(install_shell(pwsh_profile=Path(args.pwsh_profile) if args.pwsh_profile else None, dry_run=dry))
    if what in ("editor", "all"):
        results.update(install_editor(dry_run=dry))
    if what in ("agent", "all"):
        results.update(install_agent(dry_run=dry))
    if what in ("git", "all"):
        results.update(install_git_hook(Path(args.repo), dry_run=dry))
    failures = 0
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
    for path, status in results.items():
        if home and path.startswith(home):
            display = path.replace(home, "~").replace(chr(92), "/")
        else:
            try:
                rel = os.path.relpath(path).replace(chr(92), "/")
                display = rel if not rel.startswith("..") else path.replace(chr(92), "/")
            except ValueError:
                display = path.replace(chr(92), "/")
        print(f"{status:<13} {display}")
        if status.startswith("error"):
            failures += 1
    if dry:
        print("(dry run — nothing written)")
    if failures and what != "all":
        return 1
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    conn = open_db()
    try:
        insert_event(
            conn,
            ts=args.ts or _now_iso(),
            source=args.source,
            command=args.command,
            cwd=args.cwd or "",
            exit_code=args.exit,
            duration_ms=args.duration_ms,
        )
    finally:
        conn.close()
    return 0


def cmd_friday(args: argparse.Namespace) -> int:
    if args.date:
        try:
            day = dt.date.fromisoformat(args.date)
        except ValueError:
            print(f"devline: bad --date (want YYYY-MM-DD): {args.date}", file=sys.stderr)
            return 1
    else:
        day = friday_of(dt.date.today())
    conn = open_db()
    try:
        rows = query_day(conn, day)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(day_payload(day, rows), indent=2))
    else:
        print(format_day(day, rows))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="devline",
        description="Offline dev timeline: shell+editor+agent hooks into one SQLite file.",
    )
    p.add_argument("--version", action="version", version=f"devline {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    h = sub.add_parser("install-hook", help="install recording hooks (shell, editor, agent, git, all)")
    h.add_argument("target", nargs="?", choices=("shell", "editor", "agent", "git", "all"), default="all")
    h.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    h.add_argument("--pwsh-profile", help="explicit PowerShell profile path")
    h.add_argument("--repo", default=".", help="git repo for the git hook (default: .)")
    h.set_defaults(func=cmd_install_hook)

    r = sub.add_parser("record", help="record one timeline event (hooks call this)")
    r.add_argument("--source", choices=SOURCES, required=True)
    r.add_argument("--command", required=True)
    r.add_argument("--cwd", default="")
    r.add_argument("--exit", type=int, dest="exit", default=None)
    r.add_argument("--duration-ms", type=int, default=None)
    r.add_argument("--ts", help="ISO timestamp override (default: now)")
    r.set_defaults(func=cmd_record)

    f = sub.add_parser("friday", help="what did you do — timestamped evidence for a day")
    f.add_argument("--date", help="YYYY-MM-DD (default: most recent Friday)")
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=cmd_friday)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
