#!/usr/bin/env bash
#
# Fail a pull request that introduces a large blob in ANY of its commits.
#
# Why this is not a "files changed" check
# ---------------------------------------
# `main` is governed by a merge queue configured with `merge_method: MERGE`
# (ruleset 16768928). The queue therefore builds a merge commit whose second
# parent is the PR branch tip, which keeps EVERY commit on that branch reachable
# from `main` permanently. A blob added in one commit and deleted in a later one
# is still in history forever -- the final diff is clean, the repository is not.
#
# PR #910 was the live case: a 6.07 MB `actionlint` binary and a 3.8 MB
# shellcheck wheel were swept in by `git add -A`, then removed in a follow-up
# commit. Every tip-tree view -- the GitHub "Files changed" tab included --
# showed nothing. The only fix is rewriting the branch so the blob never exists.
#
# Scanning strategy
# -----------------
# `git rev-list --objects BASE..HEAD` enumerates objects reachable from HEAD but
# NOT from BASE, i.e. exactly the objects this PR introduces. Blobs already on
# `main` are excluded for free, so the repo's pre-existing large assets (the
# `whmcs/theme/six_ffc/**` bundle) never register unless a PR actually rewrites
# them -- and the allowlist covers that case.
#
# Two failures, two messages (#1243)
# -----------------------------------
# "A binary was swept in by `git add -A`" and "a text file the repo already
# tracks grew past the limit" are different mistakes with different remedies,
# and this guard used to describe only the first -- so the second was handed a
# diagnosis about somebody else's mistake, plus instructions to delete a file
# that has to stay. Each offender is therefore classified on two independent
# axes, tracked-on-base vs new, and text vs binary, and the report names the
# base size, the head size, the limit and the overage.
#
# Usage:  check-large-blobs.sh <base-ref> <head-ref>
# Env:    MAX_BLOB_BYTES     (default 1048576 = 1 MiB)
#         BLOB_ALLOWLIST     (default .github/large-blob-allowlist.txt)
# Exit:   0 = clean, 1 = oversized blob found, 2 = usage/refs error

set -euo pipefail

BASE_REF="${1:-}"
HEAD_REF="${2:-}"
MAX_BLOB_BYTES="${MAX_BLOB_BYTES:-1048576}"
BLOB_ALLOWLIST="${BLOB_ALLOWLIST:-.github/large-blob-allowlist.txt}"

if [ -z "$BASE_REF" ] || [ -z "$HEAD_REF" ]; then
  echo "usage: check-large-blobs.sh <base-ref> <head-ref>" >&2
  exit 2
fi

for ref in "$BASE_REF" "$HEAD_REF"; do
  if ! git rev-parse --verify --quiet "${ref}^{commit}" >/dev/null; then
    echo "::error::check-large-blobs: cannot resolve ref '${ref}'. The checkout" \
         "probably lacks history -- fetch both the base and head commits first." >&2
    exit 2
  fi
done

# Read allowlist patterns (glob syntax, matched with bash `==`). Blank lines and
# `#` comments are ignored. A missing file simply means "no exemptions".
patterns=()
if [ -f "$BLOB_ALLOWLIST" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    # trim surrounding whitespace
    line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -n "$line" ] && patterns+=("$line")
  done < "$BLOB_ALLOWLIST"
fi

is_allowlisted() {
  local path="$1" pat
  for pat in "${patterns[@]+"${patterns[@]}"}"; do
    # shellcheck disable=SC2053  # intentional glob match, not string equality
    if [[ "$path" == $pat ]]; then
      return 0
    fi
  done
  return 1
}

# "Is this blob text?" -- it contains no NUL byte, which is the heuristic git
# itself uses to decide whether a path gets a diff. Takes the blob's sha and the
# size `cat-file --batch-check` already reported for it, so nothing is measured
# twice. Returns 0 = text, 1 = binary, 2 = could not read.
#
# Deliberately no `head -c <window>` and no scratch file. Sniffing a window
# would mean either a pipeline whose consumer closes early -- leaving `git` with
# SIGPIPE, which `set -o pipefail` turns into a failure for every blob larger
# than the window, i.e. every blob this guard ever reports -- or a temp file,
# whose creation is one more thing that can fail on a run that is trying to
# report something else. `tr` consumes the whole stream, so neither applies, and
# reading a couple of megabytes on a run that is already failing costs nothing.
# Scanning the whole blob rather than a prefix is also strictly the more
# accurate answer; git's window exists for speed this script does not need.
is_text_blob() {
  local sha="$1" size="$2" stripped
  if ! stripped="$(git cat-file blob "$sha" | LC_ALL=C tr -d '\000' | wc -c)"; then
    return 2
  fi
  [ "$size" = "$stripped" ]
}

# Size of <path> as it stands on the base ref, or "" if the path is not there.
# A path that is absent on the base is new to this PR; a path that is present
# and smaller is a tracked file this PR grew, which is a different mistake with
# a different remedy.
size_on_base() {
  local path="$1" size
  if size="$(git cat-file -s "${BASE_REF}:${path}" 2>/dev/null)"; then
    printf '%s' "$size"
  fi
}

# `rev-list --objects` prints "<sha> [<path>]"; `cat-file --batch-check` then
# resolves type and size. Joining them keeps this to two git invocations
# regardless of how many objects the range contains.
offenders=""
allowed=""
# Which SHAPES of offender were seen. The headline and the remedy differ: a
# committed binary is removed, a tracked text file that grew past the limit is
# not -- and until #1243 the message described only the first, so every reader
# of the second was handed a diagnosis about somebody else's mistake.
grown_seen=0
grown_text_seen=0
new_seen=0

# An enumeration failure must never read as "no objects, therefore clean" -- that
# is the exact false-OK this guard exists to prevent. Both git calls are checked.
if ! object_list="$(git rev-list --objects "${BASE_REF}..${HEAD_REF}")"; then
  echo "::error::check-large-blobs: could not enumerate objects in" \
       "'${BASE_REF}..${HEAD_REF}'. The range is unscannable (incomplete history" \
       "or missing objects) -- fetch both refs at full depth and re-run." >&2
  exit 2
fi

if [ -n "$object_list" ]; then
  # Build "sha type size" for every object in the range.
  if ! sizes="$(printf '%s\n' "$object_list" \
    | awk '{print $1}' \
    | git cat-file --batch-check='%(objectname) %(objecttype) %(objectsize)')"; then
    echo "::error::check-large-blobs: could not read object metadata for" \
         "'${BASE_REF}..${HEAD_REF}'. The repository may be corrupt or shallow." >&2
    exit 2
  fi

  # Index sha -> (type, size) once. Looking each sha up by re-scanning $sizes
  # would make the scan O(n^2) in the number of objects the PR introduces.
  declare -A obj_type obj_size
  while read -r osha otype osize _rest; do
    [ -n "$osha" ] || continue
    obj_type["$osha"]="$otype"
    obj_size["$osha"]="$osize"
  done <<< "$sizes"

  while IFS= read -r line; do
    [ -z "$line" ] && continue
    sha="${line%% *}"
    rest="${line#* }"
    # Objects with no path (commits, root trees) are skipped by the type filter.
    [ "$sha" = "$rest" ] && rest=""
    path="$rest"

    otype="${obj_type[$sha]:-}"
    osize="${obj_size[$sha]:-}"

    [ "$otype" = "blob" ] || continue
    [ -n "$osize" ] || continue
    [ -n "$path" ] || continue
    [ "$osize" -gt "$MAX_BLOB_BYTES" ] || continue

    entry="$(printf '  %10s bytes  %s  (%s)' "$osize" "$path" "${sha:0:12}")"
    if is_allowlisted "$path"; then
      allowed="${allowed}${entry}"$'\n'
      continue
    fi

    # Classify the offender. This only shapes the message -- a classification
    # that cannot be determined must never drop the file from the report, so
    # every branch below still appends an entry.
    base_size="$(size_on_base "$path")"
    if is_text_blob "$sha" "$osize"; then
      kind="text file"
    elif [ "$?" = "1" ]; then
      kind="binary file"
    else
      kind="file of unreadable content"
    fi

    over=$((osize - MAX_BLOB_BYTES))
    if [ -n "$base_size" ]; then
      grown_seen=1
      if [ "$kind" = "text file" ]; then
        grown_text_seen=1
      fi
      detail="$(printf '%14sTRACKED %s that GREW: %s bytes on %s -> %s bytes here (+%s). Limit %s, over by %s.' \
        "" "$kind" "$base_size" "$BASE_REF" "$osize" "$((osize - base_size))" "$MAX_BLOB_BYTES" "$over")"
    else
      new_seen=1
      detail="$(printf '%14sNEW %s, not present on %s. Limit %s, over by %s.' \
        "" "$kind" "$BASE_REF" "$MAX_BLOB_BYTES" "$over")"
    fi
    offenders="${offenders}${entry}"$'\n'"${detail}"$'\n'
  done <<< "$object_list"
fi

if [ -n "$allowed" ]; then
  echo "Allowlisted large blobs (permitted by ${BLOB_ALLOWLIST}):"
  printf '%s' "$allowed"
  echo
fi

if [ -z "$offenders" ]; then
  echo "OK: no blob over ${MAX_BLOB_BYTES} bytes is introduced by ${BASE_REF}..${HEAD_REF}."
  exit 0
fi

if [ "$grown_seen" = 1 ] && [ "$new_seen" = 0 ]; then
  # Nothing was added -- a file the repository already tracks crossed the line.
  # Say so in the headline, because "this PR introduces a blob" reads as "you
  # committed a binary by mistake" and sends the reader looking for one.
  echo "::error::This PR pushes a file the repository already tracks past the" \
    "${MAX_BLOB_BYTES}-byte blob limit. Nothing new was committed -- an existing file grew."
else
  echo "::error::This PR introduces one or more blobs over ${MAX_BLOB_BYTES} bytes."
fi
echo "Oversized blobs introduced in this PR's commits:"
printf '%s' "$offenders"

if [ "$grown_text_seen" = 1 ]; then
  cat >&2 <<EOF

A TRACKED TEXT FILE GREW PAST THE LIMIT. That is not the mistake the rest of
this message describes: nothing was committed by accident, and there is no
binary to delete. The limit is ${MAX_BLOB_BYTES} bytes and the file is over it,
so shrink it or exempt it:

  * Shrink the file -- split it, archive the older part, or move long content
    into a linked file. Note that trimming a Markdown TABLE usually does not
    shrink it: prettier re-pads every cell to the column width.
  * If the file genuinely belongs in git at this size, add its path to
    .github/large-blob-allowlist.txt in the same PR and say why in the
    description. Also update whatever documentation states the old ceiling --
    an exemption that is not written down keeps being obeyed after it is
    lifted (#1243).

The branch rewrite below is for a blob that should never have existed. It does
not apply to a tracked file that is simply too big now.
EOF
fi

cat >&2 <<'EOF'

These may not appear in the "Files changed" tab. A blob added in one commit and
deleted in a later one is invisible there, but `main`'s merge queue uses
merge_method=MERGE, so every commit on this branch becomes permanently reachable
from `main`. Deleting the file in a follow-up commit does NOT remove it.

To fix, rewrite the branch so the blob never exists in any commit:

    git reset --soft <base>
    rm -f <the large file>
    git commit -m "<your original message>"
    git push --force-with-lease

If the file genuinely belongs in the repository, add its path to
.github/large-blob-allowlist.txt in the same PR and say why in the description.
EOF
exit 1
