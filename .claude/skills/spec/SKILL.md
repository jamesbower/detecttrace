---
name: spec
description: Turn a fuzzy feature idea into a self-contained SPEC.md by interviewing you first. Claude asks about scope, behavior, edge cases, and verification, then writes a spec a fresh implementation session can execute. Use before building anything non-trivial.
argument-hint: "[optional: one-line feature description]"
disable-model-invocation: true
allowed-tools:
  - Read
  - Grep
  - Glob
  - Write
  - Bash(git ls-files *)
  - Bash(git log *)
---

Separate deciding WHAT to build from building it. Interview the user, ground the answers in the actual codebase, then write a self-contained `SPEC.md` a fresh session can execute without you in the room. `$ARGUMENTS` is an optional starting description.

Run this in a session that already has the repo open, so the questions are informed by real code.

## Step 1: Explore enough to ask good questions

Skim the codebase for what the feature will touch: the relevant modules, existing patterns to match, the test setup, and any similar feature to model after. Read only — you're gathering context to interview well, not building.

## Step 2: Interview with AskUserQuestion

Ask only what you can't answer from the code, in focused rounds (use AskUserQuestion; batch related questions). Cover:

- **Scope & outcome** — what does "done" look like from the user's side, and what's the smallest version worth shipping?
- **Behavior** — the happy path, then the edge cases (empty, error, concurrent, permission-denied) that actually matter here.
- **Boundaries** — what is explicitly OUT of scope for this iteration.
- **Constraints** — compatibility, performance, data/migration concerns, security-sensitive surfaces.
- **Verification** — how will we confirm it works end to end (a command, a test, an observable behavior)?

Where the code implies an answer, propose it as the default in the question rather than asking cold.

## Step 3: Write SPEC.md

Write `SPEC.md` at the project root (NOT in `.claude/`). Make it self-contained — a fresh session with no memory of this conversation should be able to execute it:

```markdown
# Spec: <feature>

## Goal
<what and why, 2-3 sentences>

## Scope
- In: <what this iteration delivers>
- Out: <explicitly deferred>

## Behavior
- <happy path>
- <edge cases and how each should behave>

## Touch points
- <exact files / modules / interfaces involved, as file:path — named, not vague>

## Constraints
- <compat, performance, security, data/migration>

## Verification
- <the exact end-to-end check: command to run, test to pass, or behavior to observe>

## Open questions
- <anything still undecided — none is ideal>
```

Show the draft and confirm before writing. Then tell the user to run the implementation in a fresh session (`/clear` first) so it starts with a clean context and the spec as its brief.

## Rules

- Interview before writing — do not skip Step 2 and guess the spec.
- Name real files and interfaces from the codebase; a spec full of placeholders isn't self-contained.
- Every spec states its verification step. If you can't name one, say so and ask the user how they'll check.
- One feature per spec. If the idea is really three features, say so and spec the one that was asked.
