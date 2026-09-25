from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from devline.cli import main
from devline.db import insert_event, open_db
from devline.hooks import (
    MARK_BEGIN,
    install_agent,
    install_editor,
    install_git_hook,
    install_shell,
)
from devline.report import friday_of, project_of, summarize

SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture(autouse=True)
def _no_real_pwsh_detection(monkeypatch):
    """Never touch the machine's real PowerShell profile from tests."""
    monkeypatch.setattr("devline.hooks.detect_pwsh_profile", lambda: None)


def record(**kw):
    conn = open_db()
    try:
        return insert_event(conn, **kw)
    finally:
        conn.close()


def ev(ts, source="shell", command="pytest -q", cwd=r"C:\proj\demo", exit_code=0, duration_ms=None):
    return dict(ts=ts, source=source, command=command, cwd=cwd, exit_code=exit_code, duration_ms=duration_ms)


class TestFridayOf:
    def test_friday_returns_itself(self):
        assert friday_of(dt.date(2026, 9, 25)) == dt.date(2026, 9, 25)

    def test_saturday_steps_back_one_day(self):
        assert friday_of(dt.date(2026, 9, 26)) == dt.date(2026, 9, 25)

    def test_monday_finds_previous_friday(self):
        assert friday_of(dt.date(2026, 9, 28)) == dt.date(2026, 9, 25)

    def test_thursday_finds_friday_of_previous_week(self):
        assert friday_of(dt.date(2026, 9, 24)) == dt.date(2026, 9, 18)


class TestRecord:
    def test_inserts_and_drops_immediate_duplicates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEVLINE_DB", str(tmp_path / "t.sqlite"))
        base = ev("2026-09-18T10:00:00+00:00")
        assert record(**base) is True
        assert record(**base) is False  # same command within 3s
        assert record(**ev("2026-09-18T10:00:30+00:00")) is True  # outside window
        conn = open_db()
        try:
            assert conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 2
        finally:
            conn.close()

    def test_different_sources_never_dedupe(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEVLINE_DB", str(tmp_path / "t.sqlite"))
        assert record(**ev("2026-09-18T10:00:00+00:00", source="shell")) is True
        assert record(**ev("2026-09-18T10:00:01+00:00", source="agent")) is True


class TestFridayCommand:
    def run_friday(self, *args):
        code = main(["friday", *args])
        return code

    def test_lists_only_the_requested_day_as_evidence(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("DEVLINE_DB", str(tmp_path / "t.sqlite"))
        record(**ev("2026-09-17T09:00:00+00:00", command="day before"))
        record(**ev("2026-09-18T10:00:00+00:00", command="fix flaky test", cwd=r"C:\proj\clipcut"))
        record(**ev("2026-09-18T14:30:00+00:00", source="agent", command="apply patch", cwd=r"C:\proj\clipcut", duration_ms=5000))
        assert self.run_friday("--date", "2026-09-18") == 0
        out = capsys.readouterr().out
        assert "Friday 2026-09-18 — 2 events · 1 project" in out
        assert "fix flaky test" in out
        assert "apply patch" in out
        assert "10:00:00" in out
        assert "day before" not in out
        assert "by project:" in out
        assert "clipcut" in out

    def test_default_query_is_the_most_recent_friday(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("DEVLINE_DB", str(tmp_path / "t.sqlite"))
        today = dt.date(2026, 9, 26)  # Saturday: default friday = 2026-09-25
        record(**ev("2026-09-25T11:00:00+00:00", command="ship it"))
        record(**ev("2026-09-24T11:00:00+00:00", command="wrong day"))
        monkeypatch.setattr("devline.cli.dt.date", _FixedDate(today))
        assert self.run_friday() == 0
        out = capsys.readouterr().out
        assert "Friday 2026-09-25" in out
        assert "ship it" in out
        assert "wrong day" not in out

    def test_json_payload_shape(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("DEVLINE_DB", str(tmp_path / "t.sqlite"))
        record(**ev("2026-09-18T10:00:00+00:00", command="x", cwd=r"C:\proj\a"))
        assert self.run_friday("--date", "2026-09-18", "--json") == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["date"] == "2026-09-18"
        assert payload["summary"]["events"] == 1
        assert payload["events"][0]["command"] == "x"
        assert payload["summary"]["by_project"][0]["project"] == "a"

    def test_empty_day_hints_at_hooks(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("DEVLINE_DB", str(tmp_path / "t.sqlite"))
        assert self.run_friday("--date", "2026-09-18") == 0
        out = capsys.readouterr().out
        assert "no events on Friday 2026-09-18" in out
        assert "devline install-hook" in out

    def test_bad_date_exits_1(self, capsys):
        assert main(["friday", "--date", "not-a-date"]) == 1
        assert "bad --date" in capsys.readouterr().err


class _FixedDate:
    def __init__(self, value):
        self._value = value

    def today(self):
        return self._value

    def fromisoformat(self, s):
        return dt.date.fromisoformat(s)


class TestInstallShell:
    def test_bashrc_install_is_idempotent(self, tmp_path):
        first = install_shell(home=tmp_path, pwsh_profile=None)
        bashrc = tmp_path / ".bashrc"
        assert first[str(bashrc)] == "created"
        text = bashrc.read_text(encoding="utf-8")
        assert MARK_BEGIN in text
        assert "devline record --source shell" in text
        second = install_shell(home=tmp_path, pwsh_profile=None)
        assert second[str(bashrc)] == "already"
        assert bashrc.read_text(encoding="utf-8").count(MARK_BEGIN) == 1

    def test_appends_to_existing_rc(self, tmp_path):
        (tmp_path / ".bashrc").write_text("export EDITOR=vim\n", encoding="utf-8")
        result = install_shell(home=tmp_path, pwsh_profile=None)
        text = (tmp_path / ".bashrc").read_text(encoding="utf-8")
        assert result[str(tmp_path / ".bashrc")] == "installed"
        assert text.startswith("export EDITOR=vim")
        assert MARK_BEGIN in text

    def test_dry_run_writes_nothing(self, tmp_path):
        result = install_shell(home=tmp_path, pwsh_profile=None, dry_run=True)
        assert not (tmp_path / ".bashrc").exists()
        assert "would-install" in list(result.values())[0]

    def test_pwsh_profile_block_when_given(self, tmp_path):
        profile = tmp_path / "profile.ps1"
        profile.write_text("# mine\n", encoding="utf-8")
        result = install_shell(home=tmp_path, pwsh_profile=profile)
        assert result[str(profile)] == "installed"
        assert "function global:prompt" in profile.read_text(encoding="utf-8")


class TestInstallEditor:
    def test_vimrc_marker_idempotent(self, tmp_path):
        first = install_editor(home=tmp_path)
        assert first[str(tmp_path / ".vimrc")] == "created"
        assert "BufWritePost" in (tmp_path / ".vimrc").read_text(encoding="utf-8")
        second = install_editor(home=tmp_path)
        assert second[str(tmp_path / ".vimrc")] == "already"

    def test_dry_run(self, tmp_path):
        install_editor(home=tmp_path, dry_run=True)
        assert not (tmp_path / ".vimrc").exists()


class TestInstallAgent:
    def test_merges_into_fresh_settings(self, tmp_path):
        result = install_agent(home=tmp_path)
        path = tmp_path / ".claude" / "settings.json"
        assert result[str(path)] == "created"
        settings = json.loads(path.read_text(encoding="utf-8"))
        cmd = settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        assert "devline record --source agent" in cmd

    def test_preserves_existing_keys(self, tmp_path):
        path = tmp_path / ".claude" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"model": "opus", "hooks": {"PreToolUse": []}}), encoding="utf-8")
        install_agent(home=tmp_path)
        settings = json.loads(path.read_text(encoding="utf-8"))
        assert settings["model"] == "opus"
        assert settings["hooks"]["PreToolUse"] == []
        assert "PostToolUse" in settings["hooks"]

    def test_invalid_json_is_an_error_not_a_clobber(self, tmp_path):
        path = tmp_path / ".claude" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text("{broken", encoding="utf-8")
        result = install_agent(home=tmp_path)
        assert list(result.values())[0].startswith("error")
        assert path.read_text(encoding="utf-8") == "{broken"

    def test_second_install_is_noop(self, tmp_path):
        install_agent(home=tmp_path)
        assert list(install_agent(home=tmp_path).values())[0] == "already"


class TestInstallGit:
    @pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
    def test_hook_created_and_preserves_existing(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        hooks = tmp_path / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        pre = hooks / "post-commit"
        pre.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
        result = install_git_hook(tmp_path)
        assert result[str(pre)] in ("created", "installed")
        text = pre.read_text(encoding="utf-8")
        assert "echo custom" in text
        assert "devline record --source editor" in text
        assert install_git_hook(tmp_path)[str(pre)] == "already"

    def test_not_a_repo_is_an_error(self, tmp_path):
        result = install_git_hook(tmp_path)
        assert list(result.values())[0].startswith("error")


class TestCliWiring:
    def test_record_then_friday_end_to_end(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("DEVLINE_DB", str(tmp_path / "e2e.sqlite"))
        assert main(["record", "--source", "shell", "--command", "bun test",
                     "--cwd", r"C:\proj\devline", "--exit", "0",
                     "--ts", "2026-09-18T15:00:00+00:00"]) == 0
        assert main(["friday", "--date", "2026-09-18"]) == 0
        out = capsys.readouterr().out
        assert "bun test" in out and "(exit 0)" in out

    def test_module_entrypoint_runs(self):
        import os

        env = {**os.environ, "PYTHONPATH": SRC}
        r = subprocess.run([sys.executable, "-m", "devline.cli", "--version"],
                           capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 0
        assert "devline 0.1.0" in r.stdout

    def test_record_rejects_unknown_source(self):
        with pytest.raises(SystemExit) as exc:
            main(["record", "--source", "wat", "--command", "x"])
        assert exc.value.code == 2


class TestHelpers:
    def test_project_of_handles_slashes_and_empty(self):
        assert project_of(r"C:\proj\clipcut") == "clipcut"
        assert project_of("/home/u/repo") == "repo"
        assert project_of("") == "(no cwd)"

    def test_summarize_counts_events_and_durations(self):
        conn_rows = [
            {"cwd": "/a/one", "duration_ms": 1000, "source": "shell"},
            {"cwd": "/a/two", "duration_ms": None, "source": "agent"},
            {"cwd": "/a/one", "duration_ms": 2000, "source": "shell"},
        ]

        class Row(dict):
            def __getitem__(self, k):
                return dict.get(self, k)

        rows = [Row(r) for r in conn_rows]
        stats = summarize(rows)
        assert stats["events"] == 3
        assert stats["tracked_ms"] == 3000
        assert stats["by_project"][0]["project"] == "one"
        assert stats["by_project"][0]["events"] == 2
