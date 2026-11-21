#!/usr/bin/env python3
"""Key generation and decryption for workflow 235 (WHMCS ticket thread read).

235 reads full ticket threads but this repo is public, so it emits them only as
an envelope encrypted to a key the requester holds (AES-256-GCM content key
wrapped with RSA-OAEP-SHA256). This is the requester's half:

    # once per session: make a key pair; pass the printed line as
    # 235's `recipient_public_key` input. The private key never leaves here.
    python3 scripts/whmcs-ticket-thread-decrypt.py keygen --out /path/private.pem

    # after the run: download the artifact, then
    python3 scripts/whmcs-ticket-thread-decrypt.py decrypt --key /path/private.pem \
        whmcs-ticket-threads.enc.json > threads.json

    # or, where artifacts cannot be downloaded (cloud sessions), from the
    # job log, which carries the same ciphertext between markers:
    python3 scripts/whmcs-ticket-thread-decrypt.py decrypt-log --key /path/private.pem \
        job.log > threads.json

Keep private.pem and the decrypted output OUT of any repository: they hold
client PII. Needs the `cryptography` package.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

FORMAT = "ffc-whmcs-ticket-threads-v1"
ALG = "RSA-OAEP-SHA256+A256GCM"


def fingerprint(public_key) -> str:
    spki = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return "SHA256:" + hashlib.sha256(spki).hexdigest()


def keygen(out: str, bits: int) -> int:
    if os.path.exists(out):
        print(f"refusing to overwrite {out}", file=sys.stderr)
        return 2
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    print(f"# private key written to {out} (mode 600); fingerprint {fingerprint(key.public_key())}",
          file=sys.stderr)
    print(base64.b64encode(spki).decode("ascii"))
    return 0


def decrypt_envelope(envelope: dict, private_key) -> bytes:
    if envelope.get("format") != FORMAT or envelope.get("alg") != ALG:
        raise ValueError("not a 235 envelope (format/alg mismatch)")
    fp = fingerprint(private_key.public_key())
    if envelope.get("key_fingerprint") != fp:
        raise ValueError(
            f"envelope is for {envelope.get('key_fingerprint')}, this key is {fp}"
        )
    b = lambda k: base64.b64decode(envelope[k])  # noqa: E731
    content_key = private_key.decrypt(
        b("wrapped_key"),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    aad = f"{FORMAT}|{fp}".encode("utf-8")
    return AESGCM(content_key).decrypt(b("nonce"), b("ciphertext") + b("tag"), aad)


BEGIN_MARK = "BEGIN-FFC-TICKET-ENVELOPE"
CHUNK_MARK = "FFC-ENV "
END_MARK = "END-FFC-TICKET-ENVELOPE"


def envelope_from_log(text: str) -> dict:
    """Rebuild the envelope from 235's job log (Format-EnvelopeLogBlock).

    Each log line may carry a runner timestamp prefix, so markers are found
    anywhere in the line. The base64 is checked against the logged SHA-256
    and the chunk count before anything is decoded: a truncated or masked
    copy is refused rather than decrypted wrong.
    """
    sha = count = None
    chunks: list[str] = []
    inside = False
    for line in text.splitlines():
        if BEGIN_MARK in line:
            if inside or sha is not None:
                raise ValueError("log holds more than one envelope block")
            rest = line.split(BEGIN_MARK, 1)[1].split()
            fields = dict(f.split("=", 1) for f in rest if "=" in f)
            sha, count, inside = fields.get("sha256"), int(fields.get("chunks", "-1")), True
        elif END_MARK in line and inside:
            inside = False
        elif inside and CHUNK_MARK in line:
            chunks.append(line.split(CHUNK_MARK, 1)[1].strip())
    if sha is None:
        raise ValueError("no envelope block in this log")
    if inside:
        raise ValueError("envelope block has no end marker (log truncated?)")
    if len(chunks) != count:
        raise ValueError(f"envelope block has {len(chunks)} chunks, header says {count}")
    b64 = "".join(chunks)
    if hashlib.sha256(b64.encode("ascii")).hexdigest() != sha:
        raise ValueError("envelope block fails its SHA-256 check (masked or damaged log)")
    return json.loads(base64.b64decode(b64).decode("utf-8-sig"))


def _load_key(key_path: str):
    with open(key_path, "rb") as fh:
        return serialization.load_pem_private_key(fh.read(), password=None)


def decrypt_log(key_path: str, log_path: str) -> int:
    private_key = _load_key(key_path)
    with open(log_path, encoding="utf-8-sig", errors="replace") as fh:
        envelope = envelope_from_log(fh.read())
    return _emit(decrypt_envelope(envelope, private_key))


def decrypt(key_path: str, env_path: str) -> int:
    private_key = _load_key(key_path)
    with open(env_path, encoding="utf-8-sig") as fh:
        envelope = json.load(fh)
    return _emit(decrypt_envelope(envelope, private_key))


def _emit(plain: bytes) -> int:
    doc = json.loads(plain.decode("utf-8-sig"))
    sys.stdout.reconfigure(encoding="utf-8")
    json.dump(doc, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen", help="create a key pair; print the public key for 235's input")
    k.add_argument("--out", required=True, help="where to write the private key (PEM, mode 600)")
    k.add_argument("--bits", type=int, default=4096, choices=[3072, 4096])
    d = sub.add_parser("decrypt", help="decrypt a 235 envelope to JSON on stdout")
    d.add_argument("--key", required=True, help="private key PEM from `keygen`")
    d.add_argument("envelope", help="whmcs-ticket-threads.enc.json from the run's artifact")
    g = sub.add_parser("decrypt-log", help="decrypt the envelope copy in 235's job log")
    g.add_argument("--key", required=True, help="private key PEM from `keygen`")
    g.add_argument("log", help="the thread_read job's log text")
    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        return keygen(a.out, a.bits)
    if a.cmd == "decrypt-log":
        return decrypt_log(a.key, a.log)
    return decrypt(a.key, a.envelope)


if __name__ == "__main__":
    sys.exit(main())
