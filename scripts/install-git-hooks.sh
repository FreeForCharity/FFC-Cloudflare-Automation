#!/bin/sh
# Opt in to the FFC shared git hooks (secret / sensitive-file pre-commit scan).
# Safe to re-run. Undo with:  git config --unset core.hooksPath
set -e
root="$(git rev-parse --show-toplevel)"
git -C "$root" config core.hooksPath .githooks
echo "✓ Enabled FFC git hooks (core.hooksPath=.githooks)."
echo "  Pre-commit scans staged changes for secrets and sensitive files."
echo "  Bypass a confirmed false positive with: git commit --no-verify"
