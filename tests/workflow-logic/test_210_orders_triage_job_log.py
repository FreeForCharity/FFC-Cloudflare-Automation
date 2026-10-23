"""Guard: 210's job-log lines must carry no name and no other PII column (#1695).

210 prints the Pending-order table twice, to two sinks with different audiences,
and the two emissions deliberately carry DIFFERENT columns:

    $GITHUB_STEP_SUMMARY   order | client | name | amount | payment | date | products
                           -- `name` passed through `Format-MaskedName` ("C***")
    the job log            order=  num=  client=  date=  products=
                           -- no name at all, no amount, no payment method

The job log is the sink a cloud session can actually read: it can fetch
`actions/runs/<id>/logs` but not a step summary and not an artifact, which is
why the lines exist. It is also the least curated sink -- anyone with read
access gets the raw text, and nothing downstream redacts it.

WHY A TEST AND NOT THE COMMENT THAT IS ALREADY THERE
    The workflow says, in a comment one line above the emission:
    "a masked first name must never be used for that." That is the whole of
    today's enforcement. The predictable edit is a future reader adding
    `name=$($o.name)` back for readability -- it makes the log nicer, it breaks
    no check, and the step summary already shows a masked name so it reads as
    consistent rather than as a regression. `Format-MaskedName` would not even
    be reached: `$o.name` on the record is the FULL name, and the masking is
    applied only where the summary table is built.

    Nothing in CI can see that today. `actionlint` checks shape, CodeQL does not
    model PowerShell string interpolation into a log sink, and the PR that
    introduced the lines has no test (merge condition 4), which is what
    #1695's 2026-10-23 landing sweep reported rather than waived.

THE ALLOWLIST IS THE ASSERTION, NOT A DENYLIST
    A denylist of PII field names is only as complete as whoever last edited it,
    and the hazard here is a column that does not exist yet. So the check reads
    the exporter's OWN record shape out of `scripts/whmcs-orders-export.ps1` and
    requires every column to be classified as either log-safe or
    log-forbidden. A new column added upstream belongs to neither set and fails
    `test_every_exporter_column_is_classified` with a message naming it -- the
    classification is forced at the moment the field appears, by the person who
    added it, instead of defaulting silently in either direction.

    `ipaddress` is the one worth naming out loud: it is on the record today, it
    is personal data under GDPR, and it is exactly the kind of field a
    debugging session adds to a log line.

DISCRIMINATION IS CHECKED, NOT ASSUMED
    `test_the_scan_catches_a_leak_it_was_not_shown` runs the same extractor over
    synthetic bodies that do leak -- a plain `$($o.name)`, a masked one, an
    `$o.ipaddress`, and a second emission construct (`Write-Output`) -- and
    requires each to be reported. A guard that only ever sees the compliant text
    it was written against cannot distinguish "clean" from "not looking", which
    is this repo's recurring false-clean shape (L240).
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT, step_run  # noqa: E402

WORKFLOW = "210-whmcs-orders-triage.yml"
JOB_ID = "orders_triage"
STEP = "Export + summarize orders"

EXPORTER = REPO_ROOT / "scripts" / "whmcs-orders-export.ps1"

# PowerShell's stdout-emitting cmdlets. The job log is whatever these write;
# `Out-File` to $GITHUB_STEP_SUMMARY and the `gh issue` body are other sinks and
# are not in scope here (the summary is asserted separately, below).
EMITTERS = re.compile(r"\b(?:Write-Host|Write-Output|Write-Information|Write-Warning|Write-Error)\b")

# `$o.products`, `$($o.date)`, `$([string]$o.products)` -- the property name is
# what matters, not the wrapper, so the pattern anchors on the record variable.
RECORD_PROP = re.compile(r"\$(?:[A-Za-z_]\w*)\.([A-Za-z_]\w+)")

# Columns the job-log line may carry: enough to identify a new application
# without identifying the applicant.
LOG_SAFE = frozenset({"id", "ordernum", "userid", "date", "products"})

# Columns that must never reach the job log. `name` and `ipaddress` are personal
# data; the financial and fraud fields are not ours to broadcast to every reader
# with repo access, and `contactid` is a second identifier for the same person.
LOG_FORBIDDEN = frozenset(
    {
        "name",
        "ipaddress",
        "contactid",
        "amount",
        "paymentmethod",
        "paymentmethodname",
        "paymentstatus",
        "status",
        "fraudmodule",
        "invoiceid",
        "lineitemcount",
    }
)


def body() -> str:
    """The shipped pwsh body of 210's triage step."""
    return step_run(WORKFLOW, JOB_ID, STEP)


def exporter_columns() -> set[str]:
    """The order-record column names, read out of the exporter that writes them.

    Parsed from the single `[pscustomobject]@{ ... }` literal in
    `whmcs-orders-export.ps1`. An ambiguous or missing literal is an error, not
    an empty set: an empty set makes every classification check pass.
    """
    text = EXPORTER.read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(r"\[pscustomobject\]@\{", text, re.IGNORECASE)]
    assert len(starts) == 1, (
        f"expected exactly one [pscustomobject]@{{ }} record literal in {EXPORTER.name}, "
        f"found {len(starts)} -- the column enumeration is ambiguous, so it cannot be trusted"
    )
    depth, i = 0, text.index("{", starts[0])
    for end in range(i, len(text)):
        if text[end] == "{":
            depth += 1
        elif text[end] == "}":
            depth -= 1
            if depth == 0:
                break
    else:  # pragma: no cover - unbalanced braces in a shipped script
        raise AssertionError(f"unbalanced braces in {EXPORTER.name} record literal")
    cols = set(re.findall(r"^\s*([A-Za-z_]\w*)\s*=", text[i + 1 : end], re.MULTILINE))
    assert cols, f"no columns parsed out of {EXPORTER.name} record literal"
    return cols


def log_emission_lines(pwsh_body: str) -> list[str]:
    """Every line of `pwsh_body` that writes to the job log."""
    return [ln.strip() for ln in pwsh_body.splitlines() if EMITTERS.search(ln)]


def props_in_log(pwsh_body: str) -> set[str]:
    """Record properties interpolated into job-log emissions, lowercased.

    PowerShell property access is case-insensitive, so `$o.Name` and `$o.name`
    are the same field and must not be distinguishable here.
    """
    found: set[str] = set()
    for line in log_emission_lines(pwsh_body):
        found.update(p.lower() for p in RECORD_PROP.findall(line))
    return found


def test_the_job_log_emission_block_is_still_present():
    # The anchor for every assertion below. If the lines are renamed or removed,
    # fail loudly here rather than silently scanning text that cannot leak.
    lines = log_emission_lines(body())
    assert lines, f"no job-log emission found in {WORKFLOW} step {STEP!r}"
    pending = [ln for ln in lines if "PENDING order=" in ln]
    assert len(pending) == 1, (
        f"expected exactly one per-order job-log line, found {len(pending)}: {pending}"
    )


def test_no_pii_column_reaches_the_job_log():
    leaked = sorted(props_in_log(body()) & LOG_FORBIDDEN)
    assert not leaked, (
        f"{WORKFLOW} interpolates forbidden order field(s) {leaked} into a job-log line. "
        "The job log is readable by anyone with repo read access; the masked-name "
        "column belongs to the step summary only (#1695)."
    )


def test_the_job_log_carries_only_allowlisted_columns():
    # The positive form of the test above: a field nobody thought to forbid is
    # still a finding, because the log may carry ONLY what it needs.
    unexpected = sorted(props_in_log(body()) - LOG_SAFE)
    assert not unexpected, (
        f"{WORKFLOW} job-log line carries unallowlisted order field(s) {unexpected}. "
        f"Allowed: {sorted(LOG_SAFE)}. Add a field here only with a reason it is "
        "not personal data."
    )


def test_the_masking_helper_is_never_applied_to_a_job_log_line():
    # A masked name is the WRONG fix for this sink, not a lesser one -- the
    # workflow's own comment says so. Catching `Format-MaskedName` here stops
    # the plausible "but it's masked" edit that `$o.name` scanning alone misses
    # if the name were first assigned to a local.
    offenders = [ln for ln in log_emission_lines(body()) if "Format-MaskedName" in ln]
    assert not offenders, (
        f"a job-log emission in {WORKFLOW} applies Format-MaskedName: {offenders}. "
        "The job log carries no name at all, masked or otherwise."
    )


def test_the_step_summary_still_masks_the_name_it_does_carry():
    # The two sinks differ on purpose, so pin BOTH halves: dropping the masking
    # from the summary is the same defect reached from the other side.
    text = body()
    assert "$who = Format-MaskedName ([string]$o.name)" in text, (
        "the step-summary table no longer masks the order name through "
        "Format-MaskedName -- the summary is public too (#1695)"
    )
    assert "| $who |" in text, (
        "the masked name is computed but no longer placed in the summary table row"
    )


def test_the_exporter_really_has_a_name_column():
    # Without this, "no name in the job log" could be trivially true because no
    # such field exists, and the module would pass while asserting nothing.
    cols = exporter_columns()
    assert "name" in cols, (
        f"{EXPORTER.name} no longer emits a `name` column, so this module's "
        "premise needs re-deriving rather than silently passing"
    )
    assert "ipaddress" in cols, (
        f"{EXPORTER.name} no longer emits `ipaddress`; re-derive LOG_FORBIDDEN"
    )


def test_every_exporter_column_is_classified():
    # A column added upstream lands in neither set and is reported by name, so
    # whoever adds it decides whether it may be logged -- instead of it
    # defaulting into the allowlist (a leak) or the denylist (a false finding).
    cols = exporter_columns()
    unclassified = sorted(cols - LOG_SAFE - LOG_FORBIDDEN)
    assert not unclassified, (
        f"order record column(s) {unclassified} are classified neither log-safe nor "
        "log-forbidden. Add each to LOG_SAFE (with a reason it is not personal "
        "data) or to LOG_FORBIDDEN."
    )
    stale = sorted((LOG_SAFE | LOG_FORBIDDEN) - cols)
    assert not stale, (
        f"classified column(s) {stale} no longer exist on the exporter's record; "
        "the classification has drifted from what ships"
    )


def test_the_two_sets_do_not_overlap():
    both = sorted(LOG_SAFE & LOG_FORBIDDEN)
    assert not both, f"column(s) {both} are both log-safe and log-forbidden"


def test_the_scan_catches_a_leak_it_was_not_shown():
    # Discrimination check. Each synthetic body is a plausible future edit; all
    # four must be reported, or this module is measuring nothing.
    cases = {
        "plain name": 'Write-Host "PENDING order=$($o.id) name=$($o.name)"',
        "masked name": 'Write-Host "PENDING order=$($o.id) name=$(Format-MaskedName ([string]$o.name))"',
        "ip address": 'Write-Host "PENDING order=$($o.id) ip=$($o.ipaddress)"',
        "other emitter": 'Write-Output "PENDING order=$($o.id) amount=$($o.amount)"',
    }
    for label, line in cases.items():
        assert props_in_log(line) & LOG_FORBIDDEN, (
            f"the scan did not report the {label!r} leak: {line}"
        )
    # ...and must not report the text that actually ships.
    assert not props_in_log(body()) & LOG_FORBIDDEN, (
        "control case failed: the shipped body is reported as leaking"
    )
    # A line that writes to the summary, not the log, is out of scope and must
    # not be reported -- a detector that flags it would demand a wrong change.
    summary_line = '$lines.Add("| $($o.ordernum) | $who | $($o.amount) |")'
    assert not log_emission_lines(summary_line), (
        "a $GITHUB_STEP_SUMMARY row was read as a job-log emission"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:500]}")
    sys.exit(1 if failures else 0)
