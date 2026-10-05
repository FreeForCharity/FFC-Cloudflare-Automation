#!/usr/bin/env bash
# Invite one or more GitHub users to an organization (or update their org role).
#
# Shared by BOTH jobs of `749-org-invite-member.yml`: the ungated `preflight`
# job runs it with IN_DRY=true on the read lane (github-prod-read) to plan, and
# the gated `invite` job runs the identical file with IN_DRY=false on
# github-prod to act. One file, two credentials — so the rehearsal and the live
# run can never diverge in what they check (the 701 dns/dns_preview pattern,
# tests/workflow-logic/test_dry_run_skips_write_gates.py).
#
# Inputs arrive as environment variables, never interpolated into the body
# (#1080 / scripts/check-workflow-input-interpolation.py):
#   GH_TOKEN  a PAT. Read-scoped (members:read) is enough for a dry run; the
#             live run needs org membership WRITE (fine-grained "Members: write",
#             classic `admin:org`).
#   IN_ORG    organization login (default FreeForCharity)
#   IN_USER   one or more GitHub logins, comma/space separated
#   IN_ROLE   member | admin (default member)
#   IN_DRY    true | false (default true)
#
# API: `PUT /orgs/{org}/memberships/{username}` — for a non-member it creates
# an invitation (state=pending) and for an existing member it sets the role
# (state=active). The pre-read on the same endpoint is what lets the plan say
# "already a member" / "invitation already pending" instead of blindly
# re-sending. Both calls are idempotent, so re-running is safe.
#
# Exit: 0 when every requested user was invited / already in place (or the dry
# run planned cleanly); 1 when any user could not be processed. Per-user
# outcomes also go to $GITHUB_STEP_SUMMARY when it is set.

set -uo pipefail

# `gh api` writes its error JSON to STDOUT, so `gh … 2>/dev/null || echo <default>`
# yields the error text with the default appended rather than the default
# (ledger L02 / #854 / #889). api_get keeps stdout to successful responses
# only: on failure it returns non-zero and puts everything on stderr, so a
# caller can never report an error body as data.
api_get() {
  local out rc
  out=$(gh api "$@" 2>&1); rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '%s\n' "$out" >&2
    return "$rc"
  fi
  printf '%s' "$out"
}

trim() {
  local v="$1"
  v="${v#"${v%%[![:space:]]*}"}"
  v="${v%"${v##*[![:space:]]}"}"
  printf '%s' "$v"
}

fail() {
  echo "::error::$1"
  exit 1
}

LOGIN_RE='^[A-Za-z0-9]([A-Za-z0-9]|-[A-Za-z0-9]){0,38}$'

if [ -z "${GH_TOKEN:-}" ]; then
  fail "GH_TOKEN is not set — the Key Vault step before this one did not export a PAT."
fi

dry="$(trim "${IN_DRY:-true}" | tr '[:upper:]' '[:lower:]')"
case "$dry" in
  true | false) ;;
  *) fail "Invalid dry_run value '$dry' (expected true|false)." ;;
esac

org="$(trim "${IN_ORG:-}")"
org="${org#https://github.com/}"
org="${org#@}"
org="${org%/}"
[ -z "$org" ] && org="FreeForCharity"
if ! printf '%s' "$org" | grep -Eq "$LOGIN_RE"; then
  fail "Invalid organization login: '$org'."
fi

role="$(trim "${IN_ROLE:-member}" | tr '[:upper:]' '[:lower:]')"
[ -z "$role" ] && role="member"
case "$role" in
  member | admin) ;;
  *) fail "Invalid role '$role' (expected member|admin)." ;;
esac

if [ -z "$(trim "${IN_USER:-}")" ]; then
  fail "No username given — 'username' is required (one login, or a comma/space-separated list)."
fi

# The org must be readable by this token before we try anything per user; an
# unreadable org is a credential or scope problem, not a per-user one.
if ! org_err="$(api_get "orgs/$org" --jq '.login' 2>&1 >/dev/null)"; then
  fail "Organization '$org' is not readable with this token (${org_err:-unknown error})."
fi

echo "Org:      $org"
echo "Role:     $role"
echo "Dry run:  $dry"

# Split logins on commas / whitespace / CR, strip a leading @, dedupe.
users="$(printf '%s' "$IN_USER" | tr ',\r\n\t' '    ')"
declare -A seen
done_list=()    # invited / role set / already in place
planned=()      # dry-run only
failed=()

for raw in $users; do
  u="${raw#@}"
  [ -z "$u" ] && continue
  if [ -n "${seen[$u]:-}" ]; then continue; fi
  seen[$u]=1

  if ! printf '%s' "$u" | grep -Eq "$LOGIN_RE"; then
    echo "::error::Invalid GitHub username: '$u'."
    failed+=("$u (invalid login)")
    continue
  fi
  if ! user_err="$(api_get "users/$u" --jq '.login' 2>&1 >/dev/null)"; then
    echo "::error::GitHub user '$u' was not found (${user_err:-unknown error})."
    failed+=("$u (not found)")
    continue
  fi

  # Current membership. A 404 means "not a member and no pending invitation";
  # anything else that fails is "unknown", reported as such and never as a
  # state (L02). The live path still proceeds on unknown: the PUT is idempotent
  # and GitHub's answer to it is the authoritative state.
  state=""
  cur_role=""
  if membership="$(api_get "orgs/$org/memberships/$u" --jq '"\(.state) \(.role)"' 2>"${RUNNER_TEMP:-/tmp}/749-membership-err.$$")"; then
    state="${membership%% *}"
    cur_role="${membership#* }"
  else
    read_err="$(tr '\n' ' ' <"${RUNNER_TEMP:-/tmp}/749-membership-err.$$" 2>/dev/null)"
    if printf '%s' "$read_err" | grep -q '404'; then
      state="none"
    else
      state="unknown"
      echo "::warning::Could not read $u's membership in $org (${read_err:-unknown error}); proceeding as if unknown."
    fi
  fi
  rm -f "${RUNNER_TEMP:-/tmp}/749-membership-err.$$"

  case "$state" in
    active)
      if [ "$cur_role" = "$role" ]; then
        echo "$u is already an active $role of $org; nothing to do."
        done_list+=("$u (already $role)")
        continue
      fi
      action="change role of $u in $org from $cur_role to $role"
      ;;
    pending)
      echo "$u already has a pending invitation to $org (role $cur_role); not re-sending."
      done_list+=("$u (invitation already pending as $cur_role)")
      continue
      ;;
    *)
      action="invite $u to $org as $role"
      ;;
  esac

  if [ "$dry" = "true" ]; then
    echo "[DRY RUN] Would $action."
    planned+=("$u (would $action)")
    continue
  fi

  put_err="$(mktemp)"
  if resp="$(gh api -X PUT "orgs/$org/memberships/$u" -f role="$role" 2>"$put_err")"; then
    rm -f "$put_err"
    new_state="$(printf '%s' "$resp" | jq -r '.state // empty' 2>/dev/null || echo '')"
    new_role="$(printf '%s' "$resp" | jq -r '.role // empty' 2>/dev/null || echo '')"
    # The PUT body is not the only source of truth, and on 2026-10-02 (run
    # 36948974596) it carried no `.state` at all for a successful invitation --
    # the invite had been sent, and a later read showed `pending`. So when the
    # body yields nothing, re-read the membership the PUT should have changed
    # (the AGENTS.md rule: confirm a GitHub write by re-reading its state), and
    # only when THAT fails too fall back to the requested role, saying so.
    if [ -z "$new_state" ]; then
      if reread="$(api_get "orgs/$org/memberships/$u" --jq '"\(.state) \(.role)"' 2>/dev/null)"; then
        new_state="${reread%% *}"
        new_role="${reread#* }"
        echo "PUT for $u returned no membership state; re-read membership: state=$new_state role=$new_role."
      else
        excerpt="$(printf '%s' "$resp" | tr '\n' ' ' | cut -c1-200)"
        echo "::warning::PUT for $u returned no membership state and the re-read failed; PUT body (first 200 chars): '${excerpt}'"
      fi
    fi
    case "$new_state" in
      pending)
        echo "Invited $u to $org as ${new_role:-$role}; pending acceptance."
        done_list+=("$u (invited as ${new_role:-$role}, pending)")
        ;;
      active)
        echo "$u is now an active ${new_role:-$role} of $org."
        done_list+=("$u (${new_role:-$role})")
        ;;
      *)
        echo "::warning::PUT for $u succeeded but no membership state could be read; reporting the requested role '$role'."
        done_list+=("$u ($role, state unconfirmed)")
        ;;
    esac
  else
    api_err="$(tr '\n' ' ' <"$put_err" 2>/dev/null)"
    rm -f "$put_err"
    echo "::error::Could not invite '$u' to $org: ${api_err:-unknown error}. A 403 here usually means the PAT lacks organization members WRITE (fine-grained 'Members: write' / classic admin:org)."
    failed+=("$u")
  fi
done

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### Org invitation(s)"
    echo ""
    echo "- Org: \`$org\`"
    echo "- Requested role: \`$role\`"
    echo "- Dry run: \`$dry\`"
    if [ "${#planned[@]}" -gt 0 ]; then echo "- Planned: ${planned[*]}"; fi
    if [ "${#done_list[@]}" -gt 0 ]; then echo "- Invited/in place: ${done_list[*]}"; fi
    if [ "${#failed[@]}" -gt 0 ]; then echo "- Failed: ${failed[*]}"; fi
  } >>"$GITHUB_STEP_SUMMARY"
fi

if [ "${#failed[@]}" -gt 0 ]; then
  echo "::error::${#failed[@]} user(s) could not be processed: ${failed[*]}"
  exit 1
fi
