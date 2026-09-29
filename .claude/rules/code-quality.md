# Code Quality

## Anti-defaults (counter common Claude tendencies)

- No premature abstractions. Three similar lines beats a helper used once.
- Don't add features or improvements beyond what was asked.
- Don't refactor adjacent code while fixing a bug.
- No dead code or commented-out blocks. Git has history.
- WHY comments, never WHAT. If code needs a "what" comment, rename instead.
- API docs at module boundaries only, not every internal function.

## Naming

- Casing follows the language: see `python.md` and `frontend.md`.
- Functions verb-first. Booleans `is` / `has` / `should` / `can`. Factories `create`, converters `to`. Constants `SCREAMING_SNAKE`.
- Abbreviations only when universally known (`id`, `url`, `api`, `db`, `auth`).

## Code Markers

`TODO(author): desc (#issue)` for planned work. `FIXME(author): desc (#issue)` for known bugs. `HACK(author): desc (#issue)` for ugly workarounds (explain the proper fix). `NOTE: desc` for non-obvious context. Owner and issue link required. Never `XXX`, `TEMP`, `REMOVEME`.

## File Organization

- Imports: standard library, third-party, local. Blank line between groups.
- Function order: public API first, then helpers in call order.
