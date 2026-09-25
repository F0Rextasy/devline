# Changelog

## 0.1.0 — 2026-09-25

First release.

- `install-hook [shell|editor|agent|git|all]` — marker-guarded, idempotent hook blocks: bash/zsh + PowerShell prompt hooks, vim `BufWritePost`, Claude Code `PostToolUse` (JSON merge that preserves existing settings), git `post-commit`. `--dry-run` previews.
- `record` — one event (source, command, cwd, exit, duration) into `~/.devline/timeline.sqlite`; immediate-duplicate suppression so empty prompts never pollute the timeline.
- `friday` — most recent Friday (or `--date`), timestamped evidence list grouped by project; `--json` for scripts; exit 0 with a hooks hint when empty.
- Home-relative `~/` display in `install-hook` output; auto-detected PowerShell profiles outside the target home are never touched.
