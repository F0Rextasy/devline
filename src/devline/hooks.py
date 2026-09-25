"""Hook installation: shell (bash/zsh + pwsh), editor (vim), agent (claude)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

MARK_BEGIN = "# >>> devline >>>"
MARK_END = "# <<< devline <<<"


def _home() -> Path:
    import os

    return Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or str(Path.home()))


def _append_once(path: Path, block: str) -> str:
    """Append a marker block idempotently. Returns 'created' | 'installed' | 'already'."""
    existed = path.exists()
    text = path.read_text(encoding="utf-8") if existed else ""
    if MARK_BEGIN in text:
        return "already"
    if text and not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + block + "\n", encoding="utf-8")
    return "created" if not existed else "installed"


BASH_BLOCK = f"""{MARK_BEGIN}
__devline_record() {{
  local rc=$?
  local line id cmd
  if [ -n "${{ZSH_VERSION-}}" ]; then
    line=$(fc -l -1 2>/dev/null)
  else
    line=$(HISTTIMEFORMAT= builtin history 1 2>/dev/null)
  fi
  line="${{line#"${{line%%[![:space:]]*}}"}}"
  id="${{line%% *}}"
  cmd="${{line#* }}"
  cmd="${{cmd#"${{cmd%%[![:space:]]*}}"}}"
  if [ -n "$id" ] && [ "$id" != "${{__DEVLINE_HID-}}" ]; then
    __DEVLINE_HID=$id
    command devline record --source shell --command "$cmd" --cwd "$PWD" --exit "$rc" </dev/null >/dev/null 2>&1 || true
  fi
  return $rc
}}
if [ -n "${{ZSH_VERSION-}}" ]; then
  case " ${{precmd_functions-}} " in
    *" __devline_record "*) ;;
    *) precmd_functions+=(__devline_record) ;;
  esac
else
  case ";${{PROMPT_COMMAND-}};" in
    *";__devline_record;"*) ;;
    *) PROMPT_COMMAND="__devline_record${{PROMPT_COMMAND:+;$PROMPT_COMMAND}}" ;;
  esac
fi
{MARK_END}"""

PWSH_BLOCK = f"""# {MARK_BEGIN}
if (-not ($env:DEVLINE_PROMPT_WRAPPED)) {{
  $env:DEVLINE_PROMPT_WRAPPED = "1"
  $__devline_prev_prompt = Get-Content function:prompt -ErrorAction SilentlyContinue
  function global:prompt {{
    $h = Get-History -Count 1 -ErrorAction SilentlyContinue
    if ($h -and $env:__DEVLINE_HID -ne "$($h.Id)") {{
      $env:__DEVLINE_HID = "$($h.Id)"
      $ms = 0
      try {{ $ms = [int]($h.EndExecutionTime - $h.StartExecutionTime).TotalMilliseconds }} catch {{}}
      $code = if ($null -ne $LASTEXITCODE) {{ $LASTEXITCODE }} else {{ 0 }}
      devline record --source shell --command $h.CommandLine --cwd (Get-Location).Path --exit $code --duration-ms $ms *> $null
    }}
    if ($__devline_prev_prompt) {{ & $__devline_prev_prompt }} else {{ "PS $PWD> " }}
  }}
}}
# {MARK_END}"""

VIM_BLOCK = f"""" {MARK_BEGIN}
augroup devline
  autocmd!
  autocmd BufWritePost * call system('devline record --source editor --command "save ' . expand('%:t') . '" --cwd ' . shellescape(expand('%:p:h')))
augroup END
" {MARK_END}"""


def install_shell(home: Path | None = None, *, pwsh_profile: Path | None = None, dry_run: bool = False) -> dict[str, str]:
    h = home or _home()
    result: dict[str, str] = {}
    bashrc = h / ".bashrc"
    if dry_run:
        result[str(bashrc)] = "would-install" if MARK_BEGIN not in _safe_read(bashrc) else "already"
    else:
        result[str(bashrc)] = _append_once(bashrc, BASH_BLOCK)
    if pwsh_profile is not None:
        profile = pwsh_profile
    else:
        detected = detect_pwsh_profile()
        # Never touch a real system profile when HOME is overridden
        # (tests, sandboxes): only auto-detected profiles under this home.
        profile = (
            detected
            if detected is not None and str(detected).startswith(str(h))
            else None
        )
    if profile is not None:
        if dry_run:
            result[str(profile)] = "would-install" if MARK_BEGIN not in _safe_read(profile) else "already"
        else:
            result[str(profile)] = _append_once(profile, PWSH_BLOCK)
    return result


def detect_pwsh_profile() -> Path | None:
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", "Write-Output $PROFILE"],
            capture_output=True, text=True, timeout=15,
        )
        line = (out.stdout or "").strip().splitlines()
        if line and line[0].strip():
            return Path(line[0].strip())
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def install_editor(home: Path | None = None, dry_run: bool = False) -> dict[str, str]:
    h = home or _home()
    vimrc = h / ".vimrc"
    if dry_run:
        return {str(vimrc): "would-install" if MARK_BEGIN not in _safe_read(vimrc) else "already"}
    return {str(vimrc): _append_once(vimrc, VIM_BLOCK)}


CLAUDE_HOOK = {
    "hooks": {
        "PostToolUse": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": "devline record --source agent --command \"${CLAUDE_TOOL_NAME:-tool}\"",
                    }
                ],
            }
        ]
    }
}


def install_agent(home: Path | None = None, dry_run: bool = False) -> dict[str, str]:
    """Merge the devline hook into ~/.claude/settings.json, preserving existing keys."""
    h = home or _home()
    path = h / ".claude" / "settings.json"
    key = str(path)
    existed = path.exists()
    existing: dict = {}
    if existed:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {key: "error: settings.json is not valid JSON — fix it first"}
    if _hook_present(existing):
        return {key: "already"}
    if dry_run:
        return {key: "would-install"}
    merged = _merge_hook(existing, CLAUDE_HOOK)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return {key: "installed" if existed else "created"}


def _hook_present(settings: dict) -> bool:
    for entry in settings.get("hooks", {}).get("PostToolUse", []):
        for hook in entry.get("hooks", []):
            if "devline record" in str(hook.get("command", "")):
                return True
    return False


def _merge_hook(existing: dict, addition: dict) -> dict:
    merged = json.loads(json.dumps(existing))
    hooks = merged.setdefault("hooks", {})
    for event, entries in addition["hooks"].items():
        mine = [e for e in entries if any("devline record" in str(h.get("command", "")) for h in e.get("hooks", []))]
        hooks.setdefault(event, []).extend(mine)
    return merged


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def post_commit_hook() -> str:
    return f"""#!/bin/sh
# {MARK_BEGIN}
subj=$(git log -1 --pretty=%s 2>/dev/null)
command -v devline >/dev/null 2>&1 && devline record --source editor --command "commit: $subj" --cwd "$PWD" --exit 0 </dev/null >/dev/null 2>&1
exit 0
{MARK_END}
"""


def install_git_hook(repo: Path, dry_run: bool = False) -> dict[str, str]:
    hooks_dir = repo / ".git" / "hooks"
    if not hooks_dir.is_dir():
        return {str(repo): "error: not a git repository"}
    target = hooks_dir / "post-commit"
    existed = target.exists()
    if existed and MARK_BEGIN in target.read_text(encoding="utf-8", errors="replace"):
        return {str(target): "already"}
    if dry_run:
        return {str(target): "would-install"}
    hook = post_commit_hook()
    if existed:
        old = target.read_text(encoding="utf-8", errors="replace")
        if not old.endswith("\n"):
            old += "\n"
        hook = old + hook
    target.write_text(hook, encoding="utf-8")
    try:
        target.chmod(0o755)
    except OSError:
        pass
    return {str(target): "installed" if existed else "created"}
