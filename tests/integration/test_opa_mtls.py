"""Real TLS handshakes and zero request delivery on unauthenticated/changed peers."""

import http.client
import json
import ssl
import threading
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID

from aegisops.authorization.bundle import load_config
from aegisops.authorization.http import OpaClient, OpaError
from aegisops.authorization.projection import build_input
from aegisops.authorization.tls import client_context, load_material
from aegisops.domain.common import utc_now
from aegisops.policy.checker import PolicyChecker

pytestmark = [pytest.mark.integration, pytest.mark.safety]


@contextmanager
def tls_server(root, *, certificate=None, key=None, tls12=False):
    material = load_material(root)
    directory = material.directory
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.path)
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["input"]
            result = {
                name: request[name]
                for name in ("policy_version", "bundle_hash", "catalog_hash", "input_hash")
            }
            result.update(allow=True, autonomy_level="L0", execution_performed=False)
            body = json.dumps({"result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    if tls12:
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=str(directory / "ca.pem"))
    context.load_cert_chain(
        str(certificate or directory / "server.pem"), str(key or directory / "server-key.pem")
    )
    server.socket.settimeout(2)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield server.server_port, received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def make_request(authz_case):
    return build_input(*authz_case, PolicyChecker().evaluate(*authz_case), load_config())


def test_authenticated_decision_over_real_tls(known_pki, authz_case, monkeypatch):
    root, _ = known_pki
    monkeypatch.chdir(root)
    with tls_server(root) as (port, received):
        monkeypatch.setattr("aegisops.authorization.http.OPA_PORT", port)
        try:
            result = OpaClient().evaluate(make_request(authz_case))
        except OpaError as error:
            cause = error.__context__
            pytest.fail(
                f"TLS fixture failed: {type(cause).__name__}, errno={getattr(cause, 'errno', None)}"
            )
        assert result.allow is True
    assert received == ["/v1/data/aegisops/authz/decision"]


@pytest.mark.parametrize("fault", ["missing_client", "other_ca_client", "tls12"])
def test_unauthenticated_or_old_protocol_sends_no_http(
    known_pki, tls_operator, tmp_path, authz_case, monkeypatch, fault
):
    root, _ = known_pki
    material = load_material(root)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_verify_locations(cafile=str(material.directory / "ca.pem"))
    if fault == "other_ca_client":
        other_root = tmp_path / "other/outputs/project"
        other_root.mkdir(parents=True)
        tls_operator.issue(other_root)
        other = load_material(other_root)
        context.load_cert_chain(
            str(other.directory / "client.pem"), str(other.directory / "client-key.pem")
        )
    elif fault == "tls12":
        context, _ = client_context(root)
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        "aegisops.authorization.http.client_context", lambda: (context, material.trust)
    )
    with tls_server(root, tls12=fault == "tls12") as (port, received):
        monkeypatch.setattr("aegisops.authorization.http.OPA_PORT", port)
        with pytest.raises(OpaError):
            OpaClient().evaluate(make_request(authz_case))
    assert received == []


@pytest.mark.parametrize("fault", ["pin", "hostname", "expired"])
def test_other_server_under_same_ca_sends_no_http(
    known_pki, tls_operator, authz_case, monkeypatch, fault
):
    root, ca_key = known_pki
    material = load_material(root)
    key = ec.generate_private_key(ec.SECP256R1())
    now = utc_now()
    if fault == "expired":
        now -= timedelta(days=2)
    certificate = tls_operator.certificate(
        "wrong-name" if fault == "hostname" else "aegisops-opa",
        key,
        material.ca.subject,
        ca_key,
        now,
        now + timedelta(days=1),
        purpose=ExtendedKeyUsageOID.SERVER_AUTH,
    )
    cert_file, key_file = (
        material.directory / "other-server.pem",
        material.directory / "other-key.pem",
    )
    tls_operator.write_new(cert_file, certificate.public_bytes(serialization.Encoding.PEM))
    tls_operator.write_new(
        key_file,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    monkeypatch.chdir(root)
    with tls_server(root, certificate=cert_file, key=key_file) as (port, received):
        monkeypatch.setattr("aegisops.authorization.http.OPA_PORT", port)
        with pytest.raises(OpaError) as error:
            OpaClient().evaluate(make_request(authz_case))
        assert error.value.code == ("invalid" if fault == "pin" else "unavailable")
    assert received == []


def test_plain_http_is_not_a_decision_client(known_pki):
    root, _ = known_pki
    with tls_server(root) as (port, received):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            with pytest.raises((ConnectionError, OSError, http.client.HTTPException)):
                connection.request("POST", "/v1/data/aegisops/authz/decision", body=b"{}")
                connection.getresponse()
        finally:
            connection.close()
    assert received == []
