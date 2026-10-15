"""Pinned local identities, bounded files, validity, rotation and publication failure."""

import json
import os
import ssl
from datetime import timedelta

import pytest

from aegisops.authorization.tls import TlsError, client_context, load_material

pytestmark = [pytest.mark.unit, pytest.mark.safety]


def test_only_pinned_ca_tls13_hostname_and_client_identity(tls_root):
    context, trust = client_context(tls_root)
    assert trust == load_material(tls_root).trust
    assert context.minimum_version == ssl.TLSVersion.TLSv1_3
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert context.cert_store_stats()["x509_ca"] == 1
    directory = load_material(tls_root).directory
    assert {path.name for path in directory.iterdir()} == {
        "ca.pem",
        "server.pem",
        "client.pem",
        "server-key.pem",
        "client-key.pem",
    }
    # There is no persisted CA signing key or reuse of the audit/policy signing keys.
    assert not (directory / "ca-key.pem").exists()


@pytest.mark.parametrize("boundary", ["future", "exact_expiry", "past"])
def test_validity_boundaries_reject(tls_root, boundary):
    trust = load_material(tls_root).trust
    clock = {
        "future": trust.issued_at - timedelta(microseconds=1),
        "exact_expiry": trust.expires_at,
        "past": trust.expires_at + timedelta(seconds=1),
    }[boundary]
    with pytest.raises(TlsError):
        load_material(tls_root, now=clock)
    assert load_material(tls_root, now=trust.issued_at).trust == trust


@pytest.mark.parametrize(
    "fault", ["extra", "ca_pin", "server_pin", "client_pin", "uuid", "ttl", "duplicate", "oversize"]
)
def test_invalid_trust_never_builds_context(tls_root, fault):
    path = tls_root / ".local/authorization/tls/trust.json"
    value = json.loads(path.read_text())
    if fault == "extra":
        value["endpoint"] = "http://198.51.100.1"
    elif fault.endswith("_pin"):
        value[fault.replace("_pin", "_sha256")] = "0" * 64
    elif fault == "uuid":
        value["identity_id"] = "../../agent-credentials"
    elif fault == "ttl":
        value["expires_at"] = (
            load_material(tls_root).trust.issued_at + timedelta(hours=25)
        ).isoformat()
    text = json.dumps(value)
    if fault == "duplicate":
        text = '{"identity_id":null,"identity_id":null}'
    elif fault == "oversize":
        text = " " * 2049
    path.write_text(text)
    with pytest.raises(TlsError, match="^local TLS identity rejected$"):
        client_context(tls_root)


@pytest.mark.parametrize("file", ["ca.pem", "server.pem", "client.pem", "client-key.pem"])
@pytest.mark.parametrize("fault", ["missing", "corrupt", "oversize"])
def test_bad_material_is_bounded_and_errors_do_not_include_contents(tls_root, file, fault):
    path = load_material(tls_root).directory / file
    if fault == "missing":
        path.unlink()
    else:
        path.write_text("DO_NOT_ECHO_THIS_MATERIAL" * (500 if fault == "oversize" else 1))
    with pytest.raises(TlsError) as error:
        client_context(tls_root)
    assert str(error.value) == "local TLS identity rejected"


def test_extra_ca_certificate_is_not_added_to_trust(tls_root):
    path = load_material(tls_root).directory / "ca.pem"
    path.write_bytes(path.read_bytes() * 2)
    with pytest.raises(TlsError):
        client_context(tls_root)


@pytest.mark.parametrize("ttl", [0, 25, True, "1"])
def test_operator_rejects_invalid_ttl_before_writes(tmp_path, tls_operator, ttl):
    with pytest.raises(ValueError):
        tls_operator.issue(tmp_path, ttl_hours=ttl)
    assert not (tmp_path / ".local").exists()


def test_rotation_replaces_pins_without_overwriting_old_keys(tls_root, tls_operator):
    old = load_material(tls_root)
    old_key = (old.directory / "client-key.pem").read_bytes()
    new = tls_operator.issue(tls_root, ttl_hours=1)
    assert new.identity_id != old.trust.identity_id
    assert new.ca_sha256 != old.trust.ca_sha256
    assert load_material(tls_root).trust == new
    assert (old.directory / "client-key.pem").read_bytes() == old_key
    client_context(tls_root)


def test_failed_publication_keeps_prior_trust_and_releases_lock(
    tls_root, tls_operator, monkeypatch
):
    old = load_material(tls_root).trust

    def reject(*args):
        raise OSError("test publication failure")

    monkeypatch.setattr(tls_operator.os, "replace", reject)
    with pytest.raises(OSError):
        tls_operator.issue(tls_root)
    assert load_material(tls_root).trust == old
    assert not (tls_root / ".local/authorization/tls/.writer.lock").exists()


def test_existing_lock_is_not_taken_over(tls_root, tls_operator):
    old = load_material(tls_root).trust
    (tls_root / ".local/authorization/tls/.writer.lock").touch()
    with pytest.raises(FileExistsError):
        tls_operator.issue(tls_root)
    assert load_material(tls_root).trust == old


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission check")
def test_private_key_must_be_owner_only(tls_root):
    path = load_material(tls_root).directory / "client-key.pem"
    path.chmod(0o644)
    with pytest.raises(TlsError):
        client_context(tls_root)
