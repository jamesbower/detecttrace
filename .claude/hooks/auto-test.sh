#!/bin/bash
# Finds and runs the matching test file after Claude edits a source file.
# PostToolUse hook for Edit|Write.
# Silent on success. Only emits output when tests fail, so passing tests
# contribute zero tokens. Skips test files themselves, config files, and
# non-testable extensions.

# Requires jq for JSON parsing.
if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

# Emit failing-test output, capped so one broad failure (or a whole-suite runner
# like `go test ./...` / `cargo test`) can't flood the conversation context.
emit_test_failures() {
  echo "auto-test: failures in $REL_TEST"
  local total
  total=$(printf '%s\n' "$OUTPUT" | wc -l | tr -d ' ')
  if [ "$total" -gt 60 ]; then
    printf '%s\n' "$OUTPUT" | tail -n 60
    echo "(output truncated — $((total - 60)) earlier lines hidden; run the test file directly for full logs)"
  else
    printf '%s\n' "$OUTPUT"
  fi
}

# Test seam: fixtures set DOTCLAUDE_AUTOTEST_FAKE_LINES to exercise the failure
# emit + truncation path hermetically, since language test runners aren't
# installed in every CI environment. Mirrors the DRYRUN seam in the
# sibling hooks. Never set in normal use.
if [ -n "${DOTCLAUDE_AUTOTEST_FAKE_LINES:-}" ]; then
  REL_TEST="fake/synthetic_test"
  OUTPUT=$(seq 1 "$DOTCLAUDE_AUTOTEST_FAKE_LINES" 2>/dev/null | sed 's/^/FAIL line /')
  emit_test_failures
  exit 0
fi

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE_PATH" ] || [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

BASENAME=$(basename "$FILE_PATH")
EXTENSION="${BASENAME##*.}"
NAME="${BASENAME%.*}"
DIR=$(dirname "$FILE_PATH")

# Skip if the edited file IS a test file.
case "$BASENAME" in
  *.test.*|*.spec.*|*_test.*|*_spec.*|test_*|spec_*) exit 0 ;;
esac

# Skip config, style, and non-code files.
case "$EXTENSION" in
  json|yaml|yml|toml|ini|cfg|env|md|txt|css|scss|less|svg|png|jpg|ico|html) exit 0 ;;
esac

# Skip files in non-testable directories.
case "$FILE_PATH" in
  */.claude/*|*/public/*|*/static/*|*/assets/*|*/__mocks__/*) exit 0 ;;
esac

# Find project root.
find_project_root() {
  local dir="$PWD"
  while [ "$dir" != "/" ]; do
    if [ -f "$dir/package.json" ] || [ -f "$dir/pyproject.toml" ] || [ -f "$dir/Cargo.toml" ] || [ -f "$dir/go.mod" ] || [ -d "$dir/.git" ]; then
      echo "$dir"
      return
    fi
    dir=$(dirname "$dir")
  done
  echo "$PWD"
}

ROOT=$(find_project_root)
STEM="$NAME"

# Search for a matching test file in the usual conventions.
find_test_file() {
  local stem="$1"
  local ext="$2"

  local patterns=(
    "${stem}.test.${ext}"
    "${stem}.spec.${ext}"
    "${stem}_test.${ext}"
    "${stem}_spec.${ext}"
    "test_${stem}.${ext}"
  )

  # Same directory first.
  for pattern in "${patterns[@]}"; do
    [ -f "${DIR}/${pattern}" ] && { echo "${DIR}/${pattern}"; return; }
  done

  # __tests__ subdirectory (Jest convention).
  for pattern in "${patterns[@]}"; do
    [ -f "${DIR}/__tests__/${pattern}" ] && { echo "${DIR}/__tests__/${pattern}"; return; }
  done

  # Parallel test directory structure (src/foo.ts -> tests/foo.test.ts).
  local rel_dir="${DIR#$ROOT/}"
  local test_rel_dir
  for test_root in "tests" "test" "__tests__" "spec"; do
    test_rel_dir=$(echo "$rel_dir" | sed "s|^src/|${test_root}/|;s|^lib/|${test_root}/|")
    for pattern in "${patterns[@]}"; do
      [ -f "${ROOT}/${test_rel_dir}/${pattern}" ] && { echo "${ROOT}/${test_rel_dir}/${pattern}"; return; }
    done
  done

  # Broad search as last resort, depth-limited to stay fast.
  local found
  for pattern in "${patterns[@]}"; do
    found=$(find "$ROOT" -maxdepth 5 -name "$pattern" -not -path "*/node_modules/*" -not -path "*/.git/*" -print -quit 2>/dev/null)
    [ -n "$found" ] && { echo "$found"; return; }
  done
}

TEST_FILE=$(find_test_file "$STEM" "$EXTENSION")

if [ -z "$TEST_FILE" ]; then
  # No matching test found, not an error.
  exit 0
fi

# Make path relative for cleaner output if we end up emitting failure logs.
REL_TEST="${TEST_FILE#$ROOT/}"

# Run tests, capture output, only emit on failure.
# Use default (non-verbose) reporters to keep failure logs tight.
OUTPUT=""
EXIT=0
case "$EXTENSION" in
  js|jsx|ts|tsx|mjs|cjs)
    if [ -f "$ROOT/node_modules/.bin/vitest" ]; then
      OUTPUT=$(cd "$ROOT" && npx vitest run "$REL_TEST" 2>&1); EXIT=$?
    elif [ -f "$ROOT/node_modules/.bin/jest" ]; then
      OUTPUT=$(cd "$ROOT" && npx jest "$REL_TEST" 2>&1); EXIT=$?
    elif [ -f "$ROOT/node_modules/.bin/mocha" ]; then
      OUTPUT=$(cd "$ROOT" && npx mocha "$REL_TEST" 2>&1); EXIT=$?
    else
      OUTPUT=$(cd "$ROOT" && npm test -- "$REL_TEST" 2>&1); EXIT=$?
    fi
    ;;
  py)
    if [ -f "$ROOT/uv.lock" ] && command -v uv >/dev/null 2>&1; then
      OUTPUT=$(cd "$ROOT" && uv run pytest "$REL_TEST" 2>&1); EXIT=$?
    elif command -v pytest >/dev/null 2>&1; then
      OUTPUT=$(cd "$ROOT" && pytest "$REL_TEST" 2>&1); EXIT=$?
    elif command -v python3 >/dev/null 2>&1; then
      OUTPUT=$(cd "$ROOT" && python3 -m unittest "$REL_TEST" 2>&1); EXIT=$?
    elif command -v python >/dev/null 2>&1; then
      OUTPUT=$(cd "$ROOT" && python -m unittest "$REL_TEST" 2>&1); EXIT=$?
    fi
    ;;
  go)
    OUTPUT=$(cd "$DIR" && go test ./... 2>&1); EXIT=$?
    ;;
  rs)
    OUTPUT=$(cd "$ROOT" && cargo test 2>&1); EXIT=$?
    ;;
  *)
    exit 0
    ;;
esac

if [ "$EXIT" -ne 0 ]; then
  emit_test_failures
fi

exit 0
