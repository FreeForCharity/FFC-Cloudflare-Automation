"""Tests for 235 (WHMCS ticket thread read, encrypted) and its decrypt helper.

235 reads full ticket threads in a PUBLIC repo. The claims that carry weight:

  * READ-ONLY on the existing lane: `whmcs-prod-read` with `scope: read` on the
    READ_ALL_FFC_AZURE_* identity; the callee sends only read actions.
  * NO PLAINTEXT LEAVES THE JOB: every input arrives through env:, the upload
    step names a fixed path (no input reaches it), and the artifact is the
    encrypted envelope only.
  * The decrypt helper round-trips an envelope built to the documented format
    and refuses an envelope addressed to a different key.

The pwsh side (key validation, thread shape, envelope) is covered by Pester in
tests/whmcs-ticket-thread-read.Tests.ps1.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "235-whmcs-ticket-thread-read.yml"
CALLEE = ROOT / "scripts" / "whmcs-ticket-thread-read.ps1"
HELPER = ROOT / "scripts" / "whmcs-ticket-thread-decrypt.py"
FORMAT = "ffc-whmcs-ticket-threads-v1"


def _wf():
    with open(WORKFLOW, encoding="utf-8") as fh:
        return yaml.load(fh, Loader=yaml.BaseLoader)


def _job():
    return _wf()["jobs"]["thread_read"]


def _helper():
    spec = importlib.util.spec_from_file_location("ticket_thread_decrypt", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _envelope(public_key, plain: bytes) -> dict:
    """Build an envelope exactly as Protect-ThreadPayload does."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    spki = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    fp = "SHA256:" + hashlib.sha256(spki).hexdigest()
    key, nonce = os.urandom(32), os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plain, f"{FORMAT}|{fp}".encode())
    wrapped = public_key.encrypt(
        key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    )
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return {
        "format": FORMAT,
        "alg": "RSA-OAEP-SHA256+A256GCM",
        "key_fingerprint": fp,
        "wrapped_key": b(wrapped),
        "nonce": b(nonce),
        "tag": b(ct[-16:]),
        "ciphertext": b(ct[:-16]),
    }


def test_gated_lane_and_read_scope():
    """The GATED environment, still on the READ-scoped credential.

    Both halves matter and they pull in opposite directions, which is why one
    test asserts them together. Full ticket TEXT waits for a human approval
    (Clarke, 2026-11-12) -- unlike the ungated subject/count reads -- so the
    environment must be the gated `whmcs-prod`. But gating must NOT be reached
    by promoting the credential: `whmcs-prod` is the lane every WRITE workflow
    uses, and the obvious way to make OIDC work there is to switch to
    `scope: write` on the WR_ALL identity, which would hand a read-only
    workflow a writer PAT. 222 already pairs `environment: whmcs-prod` with
    `scope: read` on READ_ALL, so no credential change is needed, and this test
    fails if a later edit takes the easy route.
    """
    job = _job()
    assert job["environment"] == "whmcs-prod", job["environment"]
    kv = [s for s in job["steps"] if s.get("uses") == "./.github/actions/whmcs-secrets-from-kv"]
    assert len(kv) == 1, kv
    assert kv[0]["with"]["scope"] == "read"
    assert "READ_ALL_FFC_AZURE_KV_CLIENT_ID" in kv[0]["with"]["azure-client-id"]
    assert "READ_ALL_FFC_AZURE_TENANT_ID" in kv[0]["with"]["azure-tenant-id"]


def test_no_write_permissions():
    assert _wf()["permissions"] == {"contents": "read", "id-token": "write"}


def test_inputs_reach_script_only_through_env():
    for step in _job()["steps"]:
        run = step.get("run", "")
        assert "inputs." not in run, f"step {step.get('name')!r} interpolates an input into script text"
    env = [s for s in _job()["steps"] if "run" in s][0]["env"]
    assert env["RECIPIENT_PUBLIC_KEY"] == "${{ inputs.recipient_public_key }}"


def test_run_name_interpolates_no_input():
    # run-name shows in the public run list, unvalidated.
    run_name = _wf()["run-name"]
    assert "${{" not in run_name, run_name


def test_upload_is_fixed_path_encrypted_file_one_day():
    up = [s for s in _job()["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert len(up) == 1, up
    w = up[0]["with"]
    assert "inputs." not in w["path"], w["path"]
    assert w["path"].endswith("whmcs-ticket-threads.enc.json"), w["path"]
    assert w["retention-days"] == "1", w
    assert w["if-no-files-found"] == "error"


def test_callee_sends_read_actions_only():
    actions = set(re.findall(r"action\s*=\s*'(\w+)'", CALLEE.read_text(encoding="utf-8")))
    assert actions == {"GetTickets", "GetTicket", "GetTicketNotes"}, actions


def test_callee_log_lines_carry_no_thread_fields():
    # The only Write-Host / Write-Output lines in the runner are the count
    # summary and the safe error; neither may reference a thread field.
    text = CALLEE.read_text(encoding="utf-8")
    runner = text.split("if ($MyInvocation.InvocationName -eq '.') { return }", 1)[1]
    for line in runner.splitlines():
        if re.search(r"Write-(Host|Output)", line):
            for field in ("subject", "message", "email", ".name", "replies[", "notes["):
                assert field not in line, line


def test_decrypt_helper_keygen_round_trip_and_wrong_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    helper = _helper()
    with tempfile.TemporaryDirectory() as d:
        pem = pathlib.Path(d) / "k.pem"
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            assert helper.keygen(str(pem), 3072) == 0
        pub_line = out.getvalue().strip()
        assert "PRIVATE" not in pub_line
        public_key = serialization.load_der_public_key(base64.b64decode(pub_line))
        if os.name == "posix":
            assert pem.stat().st_mode & 0o777 == 0o600, oct(pem.stat().st_mode)

        doc = {"tickets": [{"tid": "240055", "replies": [{"message": "héllo SENTINEL"}]}]}
        env = _envelope(public_key, json.dumps(doc).encode("utf-8"))
        assert "SENTINEL" not in json.dumps(env)
        priv = serialization.load_pem_private_key(pem.read_bytes(), password=None)
        assert json.loads(helper.decrypt_envelope(env, priv)) == doc

        with contextlib.redirect_stderr(io.StringIO()):
            assert helper.keygen(str(pem), 3072) == 2, "keygen overwrote an existing key"

        other = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        env2 = _envelope(other.public_key(), b"{}")
        try:
            helper.decrypt_envelope(env2, priv)
        except ValueError as e:
            assert "envelope is for" in str(e), e
        else:
            raise AssertionError("decrypted an envelope addressed to another key")


TESTS = [
    test_gated_lane_and_read_scope,
    test_no_write_permissions,
    test_inputs_reach_script_only_through_env,
    test_run_name_interpolates_no_input,
    test_upload_is_fixed_path_encrypted_file_one_day,
    test_callee_sends_read_actions_only,
    test_callee_log_lines_carry_no_thread_fields,
    test_decrypt_helper_keygen_round_trip_and_wrong_key,
]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
