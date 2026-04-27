#!/usr/bin/env bash
# staged-integration-tests.sh
#
# Pre-commit hook: when an integrations/<name>.py file is staged, run the
# matching tests/integrations/test_<name>_integration.py if present.
#
# Catches API-contract / output-format mismatches before they reach main,
# where they would otherwise only surface in the advisory CI integration job.
#
# Behavior:
#   - Skips silently when no integration source files are staged.
#   - Skips files in the SHARED_MODULES set (no per-name test exists).
#   - Skips when the matching test file does not exist.
#   - Treats pytest exit code 5 (no tests collected — all skipped due to
#     missing env vars) as success with a hint pointing at .env setup.
#   - Pytest exit code 1 (real failure) blocks the commit.

set -uo pipefail

# Shared modules used by other integrations — no per-name test by convention.
SHARED_MODULES='__init__.py http.py media.py color.py tmdb.py google.py'

is_shared() {
  local name="$1"
  for shared in $SHARED_MODULES; do
    if [ "$name" = "$shared" ]; then
      return 0
    fi
  done
  return 1
}

tests_to_run=''
while IFS= read -r file; do
  [ -z "$file" ] && continue
  base="${file#integrations/}"
  if is_shared "$base"; then
    continue
  fi
  name="${base%.py}"
  test_file="tests/integrations/test_${name}_integration.py"
  if [ -f "$test_file" ]; then
    tests_to_run="$tests_to_run $test_file"
  fi
done < <(git diff --cached --name-only --diff-filter=ACM | grep -E '^integrations/[^/]+\.py$' || true)

# Trim leading whitespace.
tests_to_run="${tests_to_run# }"

if [ -z "$tests_to_run" ]; then
  exit 0
fi

echo "Running integration tests for staged integrations: $tests_to_run"

# shellcheck disable=SC2086  # intentional word-splitting for the file list
uv run pytest -m integration -v $tests_to_run
status=$?

if [ $status -eq 0 ]; then
  exit 0
fi

if [ $status -eq 5 ]; then
  echo
  echo "Integration tests skipped (missing env vars). Set up .env per AGENTS.md"
  echo "to enable local integration test coverage on commit."
  exit 0
fi

exit $status
