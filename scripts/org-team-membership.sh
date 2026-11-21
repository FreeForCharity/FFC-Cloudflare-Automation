#!/usr/bin/env bash
# List the organization's teams, or add / remove people on one team.
#
# Shared by BOTH jobs of `754-org-team-membership.yml`: the ungated `preflight`
# job runs it with IN_DRY=true on the read lane (github-prod-read) to list or to
# plan, and the gated `apply` job runs the identical file with IN_DRY=false on
# github-prod to act. One file, two credentials, so the rehearsal and the live
# run can never diverge in what they check (the 749 shape).
#
# Why this exists: FFC volunteers are recorded as verified by membership of an
# org team (ffcadmin's `next-gen-website-team`, program plan section 8), and only an
# org owner could change that by hand. This lets a thread add a new volunteer
# when Clarke approves the github-prod gate.
#
# Inputs arrive as environment variables, never interpolated into the body
# (#1080 / scripts/check-workflow-input-interpolation.py):
#   GH_TOKEN     a PAT. Read access to org members is enough for list and for a
#                dry run; add / remove / create need organization members WRITE
#                (fine-grained "Members: write", classic `admin:org`), the same
#                scope 749 uses to send org invitations.
#   IN_ORG       organization login (default FreeForCharity)
#   IN_ACTION    list | add | remove (default list)
#   IN_TEAM      team slug (required for add / remove)
#   IN_USER      one or more GitHub logins, comma/space separated (add / remove)
#   IN_ROLE      member | maintainer (team role for add; default member)
#   IN_CREATE    true | false: on add, create the team when it does not exist
#                (closed, no repository access). Default false.
#   IN_TEAM_DESCRIPTION  description used only when creating the team
#   IN_DRY       true | false (default true)
#
# APIs: `GET /orgs/{org}/teams`, `GET /orgs/{org}/teams/{slug}/members`,
# `GET|PUT|DELETE /orgs/{org}/teams/{slug}/memberships/{user}`,
# `POST /orgs/{org}/teams`. PUT and DELETE are idempotent, and each user's
# current membership is read first, so a re-run never re-sends an invitation.
# Adding someone who is not yet an org member makes GitHub send them an org
# invitation; the team membership is `pending` until they accept. Removing a
# team membership never removes org membership or any repository access.
#
# Exit: 0 when every requested user is in the requested state (or the dry run
# planned cleanly, or the list was read in full); 1 otherwise.

set -uo pipefail
# No glob expansion: the user list is split on whitespace unquoted, and a
# username of `*` must not become the names of files in the checkout.
set -f

# `gh api` writes its error JSON to STDOUT, so `gh … 2>/dev/null || echo <default>`
# yields the error text with the default appended (ledger L02). api_get keeps
# stdout to successful responses only.
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

summary() {
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '%s\n' "$@" >>"$GITHUB_STEP_SUMMARY"
  fi
}

LOGIN_RE='^[A-Za-z0-9]([A-Za-z0-9]|-[A-Za-z0-9]){0,38}$'
SLUG_RE='^[a-z0-9][a-z0-9_-]{0,99}$'
ERR_FILE="${RUNNER_TEMP:-/tmp}/754-err.$$"
trap 'rm -f "$ERR_FILE"' EXIT

if [ -z "${GH_TOKEN:-}" ]; then
  fail "GH_TOKEN is not set — the Key Vault step before this one did not export a PAT."
fi

dry="$(trim "${IN_DRY:-true}" | tr '[:upper:]' '[:lower:]')"
case "$dry" in
  true | false) ;;
  *) fail "Invalid dry_run value '$dry' (expected true|false)." ;;
esac

create="$(trim "${IN_CREATE:-false}" | tr '[:upper:]' '[:lower:]')"
case "$create" in
  true | false) ;;
  *) fail "Invalid create_team value '$create' (expected true|false)." ;;
esac

action="$(trim "${IN_ACTION:-list}" | tr '[:upper:]' '[:lower:]')"
[ -z "$action" ] && action="list"
case "$action" in
  list | add | remove) ;;
  *) fail "Invalid action '$action' (expected list|add|remove)." ;;
esac

org="$(trim "${IN_ORG:-}")"
org="${org#https://github.com/}"
org="${org#@}"
org="${org%/}"
[ -z "$org" ] && org="FreeForCharity"
if ! printf '%s' "$org" | grep -Eq "$LOGIN_RE"; then
  fail "Invalid organization login: '$org'."
fi

if ! org_err="$(api_get "orgs/$org" --jq '.login' 2>&1 >/dev/null)"; then
  fail "Organization '$org' is not readable with this token (${org_err:-unknown error})."
fi

# --- list --------------------------------------------------------------------

if [ "$action" = "list" ]; then
  if ! teams="$(api_get --paginate "orgs/$org/teams?per_page=100" \
    --jq '.[] | [.slug, .name, .privacy, (.parent.slug // "-"), ((.description // "") | gsub("[\t\n\r|]"; " "))] | @tsv' 2>"$ERR_FILE")"; then
    fail "Could not list the teams of $org ($(tr '\n' ' ' <"$ERR_FILE")). A read PAT needs organization members READ."
  fi
  count=0
  summary "### Teams in \`$org\`" "" "| Team | Name | Privacy | Parent | Members | Description |" "| --- | --- | --- | --- | --- | --- |"
  unread=0
  while IFS=$'\t' read -r slug name privacy parent desc <&3; do
    [ -z "$slug" ] && continue
    count=$((count + 1))
    if members="$(api_get --paginate "orgs/$org/teams/$slug/members?per_page=100" --jq '.[].login' 2>"$ERR_FILE")"; then
      members="$(printf '%s' "$members" | tr '\n' ' ')"
      members="$(trim "$members")"
      [ -z "$members" ] && members="(none)"
    else
      members="(could not read: $(tr '\n' ' ' <"$ERR_FILE" | cut -c1-120))"
      unread=$((unread + 1))
    fi
    echo "TEAM $slug | $name | $privacy | parent=$parent | members: $members | $desc"
    summary "| \`$slug\` | $name | $privacy | $parent | $members | $desc |"
  done 3<<<"$teams"
  echo "Listed $count team(s) in $org."
  summary "" "$count team(s)."
  if [ "$unread" -gt 0 ]; then
    fail "$unread team(s) listed without their members; the list is incomplete."
  fi
  exit 0
fi

# --- add / remove ------------------------------------------------------------

team="$(trim "${IN_TEAM:-}" | tr '[:upper:]' '[:lower:]')"
if [ -z "$team" ]; then
  fail "No team given — 'team' (the team slug) is required for $action."
fi
if ! printf '%s' "$team" | grep -Eq "$SLUG_RE"; then
  fail "Invalid team slug: '$team'."
fi

role="$(trim "${IN_ROLE:-member}" | tr '[:upper:]' '[:lower:]')"
[ -z "$role" ] && role="member"
case "$role" in
  member | maintainer) ;;
  *) fail "Invalid team role '$role' (expected member|maintainer)." ;;
esac

if [ -z "$(trim "${IN_USER:-}")" ]; then
  fail "No username given — 'username' is required for $action."
fi

echo "Org:      $org"
echo "Team:     $team"
echo "Action:   $action"
echo "Role:     $role"
echo "Dry run:  $dry"

team_exists="yes"
if ! api_get "orgs/$org/teams/$team" --jq '.slug' >/dev/null 2>"$ERR_FILE"; then
  team_err="$(tr '\n' ' ' <"$ERR_FILE")"
  if printf '%s' "$team_err" | grep -q '404'; then
    team_exists="no"
  else
    fail "Could not read team '$team' in $org (${team_err:-unknown error})."
  fi
fi

if [ "$team_exists" = "no" ]; then
  if [ "$action" = "remove" ]; then
    fail "Team '$team' does not exist in $org, so there is nobody to remove. Run with action=list to see the team slugs."
  fi
  if [ "$create" != "true" ]; then
    fail "Team '$team' does not exist in $org. Run with action=list to see the team slugs, or set create_team=true to create it (closed, no repository access)."
  fi
  desc="$(trim "${IN_TEAM_DESCRIPTION:-}")"
  [ -z "$desc" ] && desc="Verified FFC volunteers. Membership is the verification record; it grants no repository access."
  if [ "$dry" = "true" ]; then
    echo "[DRY RUN] Would create team '$team' in $org (closed, no repository access)."
    summary "### Team \`$team\` in \`$org\`" "" "- Would create the team (closed, no repository access)."
  else
    # GitHub adds the token's owner (the org owner whose PAT this is) to a new
    # team as its maintainer. That is expected: the owner is the verifier.
    if ! new_slug="$(gh api -X POST "orgs/$org/teams" -f name="$team" -f privacy=closed -f description="$desc" \
      --jq '.slug' 2>"$ERR_FILE")"; then
      fail "Could not create team '$team' in $org: $(tr '\n' ' ' <"$ERR_FILE"). A 403 usually means the PAT lacks organization members WRITE."
    fi
    new_slug="$(trim "$new_slug")"
    if [ -n "$new_slug" ] && [ "$new_slug" != "$team" ]; then
      echo "::warning::GitHub gave the new team the slug '$new_slug' (asked for '$team'); using '$new_slug'."
      team="$new_slug"
    fi
    echo "Created team '$team' in $org (closed, no repository access)."
    summary "### Team \`$team\` in \`$org\`" "" "- Created the team (closed, no repository access)."
  fi
else
  summary "### Team \`$team\` in \`$org\`" ""
fi

users="$(printf '%s' "$IN_USER" | tr ',\r\n\t' '    ')"
declare -A seen
done_list=()
planned=()
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
    if printf '%s' "$user_err" | grep -q '404'; then
      echo "::error::GitHub user '$u' was not found."
      failed+=("$u (not found)")
    else
      echo "::error::Could not look up GitHub user '$u' (${user_err:-unknown error})."
      failed+=("$u (lookup failed)")
    fi
    continue
  fi

  # Current team membership. 404 = not on the team. Anything else that fails
  # is "unknown" and is never reported as a state (L02).
  state="none"
  cur_role=""
  if [ "$team_exists" = "yes" ]; then
    if m="$(api_get "orgs/$org/teams/$team/memberships/$u" --jq '"\(.state) \(.role)"' 2>"$ERR_FILE")"; then
      state="${m%% *}"
      cur_role="${m#* }"
    else
      read_err="$(tr '\n' ' ' <"$ERR_FILE")"
      if printf '%s' "$read_err" | grep -q '404'; then
        state="none"
      else
        state="unknown"
        echo "::warning::Could not read $u's membership of $team (${read_err:-unknown error}); proceeding as if unknown."
      fi
    fi
  fi

  if [ "$action" = "add" ]; then
    case "$state" in
      active)
        if [ "$cur_role" = "$role" ]; then
          echo "$u is already an active $role of $team; nothing to do."
          done_list+=("$u (already $role)")
          continue
        fi
        plan="change $u's role on $team from $cur_role to $role"
        ;;
      pending)
        if [ "$cur_role" = "$role" ]; then
          echo "$u is already pending on $team (role $cur_role, waiting for them to accept the org invitation); not re-sending."
          done_list+=("$u (already pending as $cur_role)")
          continue
        fi
        plan="change $u's pending role on $team from $cur_role to $role"
        ;;
      *) plan="add $u to $team as $role" ;;
    esac
  else
    case "$state" in
      none)
        echo "$u is not on $team; nothing to remove."
        done_list+=("$u (not on the team)")
        continue
        ;;
      *) plan="remove $u from $team (org membership and repository access are not touched)" ;;
    esac
  fi

  if [ "$dry" = "true" ]; then
    echo "[DRY RUN] Would $plan."
    planned+=("$u (would $plan)")
    continue
  fi

  if [ "$action" = "add" ]; then
    if resp="$(gh api -X PUT "orgs/$org/teams/$team/memberships/$u" -f role="$role" 2>"$ERR_FILE")"; then
      new_state="$(printf '%s' "$resp" | jq -r '.state // empty' 2>/dev/null || echo '')"
      if [ -z "$new_state" ]; then
        # Confirm a write by re-reading the state it should have changed.
        new_state="$(api_get "orgs/$org/teams/$team/memberships/$u" --jq '.state' 2>/dev/null || echo '')"
      fi
      case "$new_state" in
        active)
          echo "$u is now an active $role of $team."
          done_list+=("$u ($role)")
          ;;
        pending)
          echo "$u was added to $team as $role; pending until they accept the org invitation."
          done_list+=("$u ($role, pending org invitation)")
          ;;
        *)
          echo "::warning::PUT for $u succeeded but no membership state could be read; reporting the requested role '$role'."
          done_list+=("$u ($role, state unconfirmed)")
          ;;
      esac
    else
      echo "::error::Could not add '$u' to $team: $(tr '\n' ' ' <"$ERR_FILE"). A 403 usually means the PAT lacks organization members WRITE (fine-grained 'Members: write' / classic admin:org)."
      failed+=("$u")
    fi
  else
    if gh api -X DELETE "orgs/$org/teams/$team/memberships/$u" >/dev/null 2>"$ERR_FILE"; then
      echo "Removed $u from $team."
      done_list+=("$u (removed)")
    else
      echo "::error::Could not remove '$u' from $team: $(tr '\n' ' ' <"$ERR_FILE")."
      failed+=("$u")
    fi
  fi
done

summary "- Action: \`$action\`, role \`$role\`, dry run \`$dry\`"
if [ "${#planned[@]}" -gt 0 ]; then summary "- Planned: ${planned[*]}"; fi
if [ "${#done_list[@]}" -gt 0 ]; then summary "- Done / in place: ${done_list[*]}"; fi
if [ "${#failed[@]}" -gt 0 ]; then summary "- Failed: ${failed[*]}"; fi

if [ "${#failed[@]}" -gt 0 ]; then
  echo "::error::${#failed[@]} user(s) could not be processed: ${failed[*]}"
  exit 1
fi
exit 0
