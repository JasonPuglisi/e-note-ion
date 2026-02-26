#!/usr/bin/env bash
# check-version-bump.sh
#
# Pre-commit hook: warn when .py files are staged without a version bump.
#
# Exits 1 if Python source files are staged but pyproject.toml's version
# line is unchanged in the index. Bypassable with --no-verify for commits
# that are intentionally not release-worthy (test-only, dev tooling, etc.).

set -euo pipefail

# Check whether any staged file is a Python source file (not test/tooling).
# We match any .py file; the committer uses --no-verify to bypass when the
# change is genuinely not release-worthy.
if ! git diff --cached --name-only | grep -q '\.py$'; then
  exit 0
fi

# Check whether pyproject.toml's version line appears in the staged diff.
if git diff --cached -- pyproject.toml | grep -q '^+version = '; then
  exit 0
fi

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Version bump missing                                        ║"
echo "║                                                              ║"
echo "║  .py files are staged but pyproject.toml version is         ║"
echo "║  unchanged. If this commit is release-worthy, bump the      ║"
echo "║  version before committing.                                  ║"
echo "║                                                              ║"
echo "║  Not release-worthy? Bypass with: git commit --no-verify    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
exit 1
