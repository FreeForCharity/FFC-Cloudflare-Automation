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


def decrypt(key_path: str, env_path: str) -> int:
    with open(key_path, "rb") as fh:
        private_key = serialization.load_pem_private_key(fh.read(), password=None)
    with open(env_path, encoding="utf-8-sig") as fh:
        envelope = json.load(fh)
    plain = decrypt_envelope(envelope, private_key)
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
    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        return keygen(a.out, a.bits)
    return decrypt(a.key, a.envelope)


if __name__ == "__main__":
    sys.exit(main())
