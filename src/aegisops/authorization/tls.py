"""Local pinned OPA identities. No issuer, network calls or plaintext fallback here."""

import hashlib
import json
import os
import ssl
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pydantic import model_validator

from aegisops.domain.common import DTO, Sha256, UTCDateTime, utc_now
from aegisops.io import read_text, unique_object

CLIENT_SUBJECT = "aegisops-pep"
SERVER_SUBJECT = "aegisops-opa"


class TlsError(ValueError):
    def __init__(self) -> None:
        super().__init__("local TLS identity rejected")


class TlsTrust(DTO):
    format: Literal["aegisops-opa-mtls-v1"] = "aegisops-opa-mtls-v1"
    identity_id: UUID
    ca_sha256: Sha256
    server_sha256: Sha256
    client_sha256: Sha256
    issued_at: UTCDateTime
    expires_at: UTCDateTime

    @model_validator(mode="after")
    def lifetime(self) -> Self:
        if not timedelta(0) < self.expires_at - self.issued_at <= timedelta(hours=24):
            raise TlsError()
        if len({self.ca_sha256, self.server_sha256, self.client_sha256}) != 3:
            raise TlsError()
        return self


def safe_path(path: Path) -> Path:
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise TlsError()
    return path


def fingerprint(certificate: x509.Certificate) -> str:
    return hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()


def read_certificate(path: Path) -> x509.Certificate:
    data = read_text(safe_path(path), 8192).encode("ascii")
    certificate = x509.load_pem_x509_certificate(data)
    if data != certificate.public_bytes(serialization.Encoding.PEM):
        raise TlsError()
    return certificate


@dataclass(frozen=True)
class TlsMaterial:
    trust: TlsTrust
    directory: Path
    ca: x509.Certificate


def load_material(root: Path | None = None, *, now: datetime | None = None) -> TlsMaterial:
    try:
        root = safe_path(root or Path.cwd())
        raw = read_text(safe_path(root / ".local/authorization/tls/trust.json"), 2048)
        trust = TlsTrust.model_validate(json.loads(raw, object_pairs_hook=unique_object))
        clock = now if now is not None else utc_now()
        if not trust.issued_at <= clock < trust.expires_at:
            raise TlsError()
        directory = safe_path(root.parents[1] / "work/opa-mtls" / str(trust.identity_id))
        ca = read_certificate(directory / "ca.pem")
        server = read_certificate(directory / "server.pem")
        client = read_certificate(directory / "client.pem")
        for certificate, expected in (
            (ca, trust.ca_sha256),
            (server, trust.server_sha256),
            (client, trust.client_sha256),
        ):
            if fingerprint(certificate) != expected:
                raise TlsError()
            if not certificate.not_valid_before_utc <= clock < certificate.not_valid_after_utc:
                raise TlsError()
            if certificate.not_valid_after_utc < trust.expires_at:
                raise TlsError()
            certificate.verify_directly_issued_by(ca)
        if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise TlsError()
        for certificate, subject, purpose in (
            (server, SERVER_SUBJECT, ExtendedKeyUsageOID.SERVER_AUTH),
            (client, CLIENT_SUBJECT, ExtendedKeyUsageOID.CLIENT_AUTH),
        ):
            if certificate.subject != x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]):
                raise TlsError()
            if certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise TlsError()
            if set(certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) != {
                purpose
            }:
                raise TlsError()
        return TlsMaterial(trust, directory, ca)
    except Exception:
        raise TlsError() from None


def client_context(root: Path | None = None) -> tuple[ssl.SSLContext, TlsTrust]:
    try:
        material = load_material(root)
        key = safe_path(material.directory / "client-key.pem")
        if os.name != "nt" and (
            stat.S_IMODE(key.stat().st_mode) != 0o600
            or key.stat().st_uid != getattr(os, "getuid")()
        ):
            raise TlsError()
        # Bound the key before OpenSSL reads it. The trusted local operator owns these files.
        read_text(key, 8192)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.verify_flags |= ssl.VERIFY_X509_STRICT
        context.load_verify_locations(
            cadata=material.ca.public_bytes(serialization.Encoding.PEM).decode("ascii")
        )
        context.load_cert_chain(str(material.directory / "client.pem"), str(key))
        return context, material.trust
    except Exception:
        raise TlsError() from None
