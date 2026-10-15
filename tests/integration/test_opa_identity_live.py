"""Real OPA: a client signed by the CA still needs the exact authorized identity."""

import http.client
import os
import socket
import subprocess  # noqa: TID251 -- isolated test process, never application code
import time
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID

from aegisops.authorization.tls import client_context, load_material
from aegisops.domain.common import utc_now

pytestmark = [
    pytest.mark.integration,
    pytest.mark.opa_live,
    pytest.mark.skipif(
        os.environ.get("AEGISOPS_OPA_LIVE") != "1", reason="explicit real OPA opt-in required"
    ),
]


@pytest.fixture
def isolated_opa(known_pki):
    root, _ = known_pki
    repo = Path(__file__).resolve().parents[2]
    binary = Path(
        os.environ.get("AEGISOPS_OPA_BIN", str(repo.parents[1] / "work/tooling/opa/opa.exe"))
    )
    if not binary.is_file():
        pytest.fail("Explicit real OPA runtime not found")
    directory = load_material(root).directory
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    arguments = [
        str(binary),
        "run",
        "--server",
        f"--addr=https://127.0.0.1:{port}",
        "--authorization=basic",
        "--authentication=tls",
        "--min-tls-version=1.3",
        f"--tls-cert-file={directory / 'server.pem'}",
        f"--tls-private-key-file={directory / 'server-key.pem'}",
        f"--tls-ca-cert-file={directory / 'ca.pem'}",
        "--disable-telemetry",
        "--log-level=error",
        str(repo / "policies/opa/decision.rego"),
        str(repo / "policies/opa/api_authz.rego"),
        str(repo / "policies/opa/data.json"),
    ]
    process = subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and process.poll() is None:
            context, _ = client_context(root)
            connection = http.client.HTTPSConnection(
                "127.0.0.1", port, context=context, timeout=0.3
            )
            try:
                connection.request("POST", "/v1/data/aegisops/authz/decision", body=b"{}")
                response = connection.getresponse()
                if response.status == 200:
                    response.read()
                    break
            except OSError:
                time.sleep(0.05)
            finally:
                connection.close()
        else:
            pytest.fail("Isolated OPA mTLS startup/readiness failed")
        yield port
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


@pytest.mark.parametrize("subject,expected", [("aegisops-pep", 200), ("other-client", 401)])
def test_real_opa_checks_certificate_subject_not_headers(
    known_pki, tls_operator, isolated_opa, subject, expected
):
    root, ca_key = known_pki
    material = load_material(root)
    key = ec.generate_private_key(ec.SECP256R1())
    now = utc_now()
    certificate = tls_operator.certificate(
        subject,
        key,
        material.ca.subject,
        ca_key,
        now,
        now + timedelta(hours=1),
        purpose=ExtendedKeyUsageOID.CLIENT_AUTH,
    )
    cert_file, key_file = material.directory / "probe.pem", material.directory / "probe-key.pem"
    tls_operator.write_new(cert_file, certificate.public_bytes(serialization.Encoding.PEM))
    tls_operator.write_new(
        key_file,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    context, _ = client_context(root)
    context.load_cert_chain(str(cert_file), str(key_file))
    connection = http.client.HTTPSConnection("127.0.0.1", isolated_opa, context=context, timeout=2)
    try:
        connection.request(
            "POST",
            "/v1/data/aegisops/authz/decision",
            body=b"{}",
            headers={"Identity": "CN=aegisops-pep", "Authorization": "Bearer CN=aegisops-pep"},
        )
        response = connection.getresponse()
        assert response.status == expected
        response.read()
    finally:
        connection.close()
