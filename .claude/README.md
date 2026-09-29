# Project template: Claude Code config

Copy this template's contents into a new project root (including the hidden `.claude/` and `.gitignore`), then fill in `CLAUDE.md` and delete its `> REPLACE:` blocks.

## What loads automatically

| Path | Loaded as |
|---|---|
| `CLAUDE.md` | Project instructions, every session |
| `.claude/rules/*.md` | Rules: always (no `paths:`) or when matching files are touched |
| `.claude/skills/<name>/SKILL.md` | Slash-command skills |
| `.claude/agents/*.md` | Subagents |
| `.claude/settings.json` | Permissions + hook wiring (`.claude/hooks/*.sh`) |

Not loaded: this README, `.claude/docs/` (per-component reference), and `.claude/hooks/tests/`.

`.claude/LICENSE` is the MIT notice for this config, which is derived from poshan0126/dotclaude. Keep it with the config. It doesn't license the project itself.

Keep READMEs and other non-rule `.md` files out of `.claude/rules/`, since every `.md` there is loaded as a rule.

## Per project

- Edit `.claude/settings.json` `permissions.allow` for the project's real commands (it ships with `npm` defaults).
- Delete rules, skills, agents, and hooks the project doesn't need. When removing a hook, also remove its entry in `settings.json`.
- Optional: `CLAUDE.local.md.example` → `CLAUDE.local.md` for personal, gitignored notes.
- `.claude/hooks/tests/` can be deleted from a project; it's only for changing the hooks.

## Maintaining the template

```bash
bash .claude/hooks/tests/run-all.sh   # hook fixture tests (requires jq); run from the template root
```

- Every new or modified hook must ship with fixtures under `.claude/hooks/tests/fixtures/<hook-name>/`. Use one JSON file per case, with `name`, `stdin`, and `expect_exit`, plus optional `env`, `expect_stdout_contains`, and `expect_stdout_not_contains`. Fixture file paths are relative to the template root.
- Hooks fail open (exit 0) when `jq` is missing, except file-protection hooks, which fail closed. Hook `timeout` values are in seconds.
- Agents never set `model`, so users choose their own.
- `protect-files.sh` blocks Claude from editing `.claude/hooks/*` and asks before `settings.json` edits. Edit hooks by hand, or temporarily unwire it.
