"""Local operator only: issue short-lived, pinned OPA/PEP identities; no network."""

import argparse
import csv
import io
import ipaddress
import os
import re
import subprocess
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from aegisops.authorization.tls import (
    CLIENT_SUBJECT,
    SERVER_SUBJECT,
    TlsTrust,
    client_context,
    fingerprint,
    load_material,
    safe_path,
)
from aegisops.domain.common import utc_now


def certificate(subject, key, issuer, issuer_key, now, expires, *, ca=False, purpose=None):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer or name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(expires)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
    )
    if purpose is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage([purpose]), critical=False)
    if subject == SERVER_SUBJECT:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
    return builder.sign(issuer_key, hashes.SHA256())


def write_new(path, data):
    descriptor = os.open(safe_path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def restrict_directory(directory):
    if os.name == "nt":
        # Current user's SID, not a localized name; fixed program/arguments, never a shell.
        system = Path(os.environ["SystemRoot"]) / "System32"
        account = subprocess.run(
            [str(system / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            check=False,
        )
        rows = list(csv.reader(io.StringIO(account.stdout)))
        if account.returncode or len(rows) != 1 or len(rows[0]) != 2:
            raise ValueError()
        sid = rows[0][1]
        if re.fullmatch(r"S-1-(?:\d+-)+\d+", sid) is None:
            raise ValueError()
        result = subprocess.run(
            [
                str(system / "icacls.exe"),
                str(directory),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(OI)(CI)F",
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise ValueError()
    else:
        directory.chmod(0o700)


def issue(root, *, ttl_hours=24, clock=utc_now):
    if type(ttl_hours) is not int or not 1 <= ttl_hours <= 24:
        raise ValueError()
    root = safe_path(root)
    state = safe_path(root / ".local/authorization/tls")
    state.mkdir(parents=True, exist_ok=True)
    # No lock takeover and no silent trust reset after a partial/corrupt publication.
    lock = state / ".writer.lock"
    with lock.open("xb") as lock_handle:
        try:
            if (state / "trust.json").exists():
                # Expired material may be rotated, but existing pinned content must still match.
                import json

                from aegisops.io import read_text, unique_object

                old = TlsTrust.model_validate(
                    json.loads(
                        read_text(state / "trust.json", 2048), object_pairs_hook=unique_object
                    )
                )
                load_material(root, now=old.issued_at)
            now = clock().replace(microsecond=0)
            expires = now + timedelta(hours=ttl_hours)
            ca_key, server_key, client_key = (
                ec.generate_private_key(ec.SECP256R1()) for _ in range(3)
            )
            ca = certificate("aegisops-local-ca", ca_key, None, ca_key, now, expires, ca=True)
            server = certificate(
                SERVER_SUBJECT,
                server_key,
                ca.subject,
                ca_key,
                now,
                expires,
                purpose=ExtendedKeyUsageOID.SERVER_AUTH,
            )
            client = certificate(
                CLIENT_SUBJECT,
                client_key,
                ca.subject,
                ca_key,
                now,
                expires,
                purpose=ExtendedKeyUsageOID.CLIENT_AUTH,
            )
            trust = TlsTrust(
                identity_id=uuid4(),
                ca_sha256=fingerprint(ca),
                server_sha256=fingerprint(server),
                client_sha256=fingerprint(client),
                issued_at=now,
                expires_at=expires,
            )
            directory = safe_path(root.parents[1] / "work/opa-mtls" / str(trust.identity_id))
            directory.mkdir(parents=True, exist_ok=False)
            restrict_directory(directory)
            for name, cert in (("ca", ca), ("server", server), ("client", client)):
                write_new(directory / f"{name}.pem", cert.public_bytes(serialization.Encoding.PEM))
            for name, key in (("server", server_key), ("client", client_key)):
                write_new(
                    directory / f"{name}-key.pem",
                    key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    ),
                )
            # The CA signing key is never persisted. Rotation creates a fresh CA and pins.
            temporary = state / f"trust-{uuid4()}.tmp"
            write_new(temporary, trust.model_dump_json(indent=2).encode())
            try:
                os.replace(temporary, safe_path(state / "trust.json"))
            finally:
                if temporary.exists():
                    temporary.unlink()
            return trust
        finally:
            lock_handle.close()
            lock.unlink()


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("issue", "check", "paths"))
    parser.add_argument("--ttl-hours", type=int, default=24)
    args = parser.parse_args()
    try:
        if args.command == "issue":
            trust = issue(root, ttl_hours=args.ttl_hours)
            print(f"OPA mTLS issued; expires {trust.expires_at.isoformat()}. Restart owned OPA.")
        else:
            material = load_material(root)
            client_context(root)
            if args.command == "paths":
                # Paths only, never PEM or private key contents; operator launcher/CI consumes this.
                print(material.directory)
            else:
                print("OPA pinned mTLS identity valid; no network request or permission granted.")
    except Exception:
        raise SystemExit(
            "OPA TLS operation rejected; no trust reset. Check local identity and permissions."
        ) from None


if __name__ == "__main__":
    main()
