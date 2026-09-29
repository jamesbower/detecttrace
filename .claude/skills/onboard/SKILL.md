---
name: onboard
description: Get oriented in an unfamiliar codebase fast. An Explore-subagent-driven tour that goes from a one-paragraph overview down to architecture, data models, auth, conventions, and a glossary — without flooding your main context. Use on a repo you're new to.
argument-hint: "[optional: focus area like 'the billing module' or 'auth']"
disable-model-invocation: true
---

Build a mental model of a codebase you're new to — broad first, then narrowing — the way the Claude Code onboarding recipe prescribes. Read only; never modify anything. Delegate the heavy reading to **Explore** subagents so their file dumps stay out of this conversation and only the summaries return.

## Orientation (injected — already in front of you)

Top level:

```!
ls -1 "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null | head -40
```

Manifests / entry points present at the root:

```!
ls -1 "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null | grep -iE '^(package\.json|pyproject\.toml|go\.mod|cargo\.toml|gemfile|composer\.json|pom\.xml|build\.gradle|makefile|dockerfile|readme)' || echo "(no standard manifest at root — this may be a subdir or an unusual layout)"
```

## Step 1: Dispatch the tour

Launch **Explore** subagents (in parallel where independent) to answer these, starting broad and narrowing. If `$ARGUMENTS` names a focus area, weight every prompt toward it.

1. **Overview** — what this project is, who uses it, the top-level layout and what each major directory holds. Point the agent at the README and manifests first.
2. **Architecture** — the main patterns, the entry points, how one request (or CLI invocation) flows end to end, and the module boundaries (what must not depend on what).
3. **Data models** — the core domain types/entities/tables and their relationships, and where they're defined.
4. **Auth & security** — how authentication and authorization work, where secrets/config load from, and the trust boundaries.
5. **Conventions** — naming, file layout, test style and framework, error-handling and logging patterns, and the real build/test/lint commands from the manifests.

Each Explore prompt must ask for a tight summary with `file:path` pointers, NOT pasted file contents.

## Step 2: Synthesize the brief

Merge the returns into one scannable onboarding brief — nothing pasted verbatim, everything a pointer:

```
# Onboarding: <project>

**What it is**: <2-3 sentences>
**Stack**: <languages, frameworks, key libraries>
**Architecture**: <the 3-5 load-bearing facts + entry points, as file:path>
**Data model**: <core entities and where they live>
**Auth & boundaries**: <how auth works, where secrets load>
**Conventions**: <naming, tests, error handling — the house style>
**Run it**: <build / test / lint commands>
**Glossary**: <project-specific terms an outsider wouldn't know>
**Start here**: <the 2-3 files to read first for the focus area>
```

If the repo is empty or trivial (no source beyond config), say so plainly instead of padding the brief.

## Rules

- Strictly read-only. This skill maps the code; it never changes it.
- Summaries and `file:line` pointers only — never paste large files or diffs into the brief.
- Prefer several focused Explore subagents over reading everything in the main context; keeping exploration out of this conversation is the whole point.
- Don't invent structure you didn't verify. If an area (e.g. auth) doesn't exist, say "none found" — don't fabricate one.
