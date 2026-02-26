#!/usr/bin/env bash
# check-version-bump.sh
#
# Pre-commit hook: warn when .py or .json files are staged without a version bump.
#
# Exits 1 if Python source files or content JSON files are staged but
# pyproject.toml's version line is unchanged in the index. Bypassable with
# --no-verify for commits that are intentionally not release-worthy
# (test-only, dev tooling, etc.).

set -euo pipefail

# Check whether any staged file is a Python source file or a JSON file.
# We match any .py or .json file; the committer uses --no-verify to bypass
# when the change is genuinely not release-worthy (e.g. tooling config JSON).
if ! git diff --cached --name-only | grep -qE '\.(py|json)$'; then
  exit 0
fi

# Check whether pyproject.toml's version line appears in the staged diff.
if git diff --cached -- pyproject.toml | grep -q '^+version = '; then
  exit 0
fi

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Version bump missing                                        ║"
echo "║                                                              ║"
echo "║  .py or .json files are staged but pyproject.toml version   ║"
echo "║  is unchanged. If this commit is release-worthy, bump the  ║"
echo "║  version before committing.                                  ║"
echo "║                                                              ║"
echo "║  Not release-worthy? Bypass with: git commit --no-verify    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
exit 1
