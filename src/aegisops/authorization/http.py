"""A single fixed loopback OPA decision endpoint, no proxies, tools or management API."""

import hashlib
import http.client
import json
import socket
import threading
import time
from typing import Literal

from aegisops.authorization.tls import client_context, load_material
from aegisops.domain.authorization import OpaInput, OpaResult
from aegisops.io import unique_object

OPA_HOST = "127.0.0.1"
OPA_PORT = 18181
OPA_PATH = "/v1/data/aegisops/authz/decision"


class OpaError(ValueError):
    def __init__(self, code: Literal["unavailable", "invalid"] = "invalid") -> None:
        self.code = code
        super().__init__("local policy service rejected")


class OpaClient:
    def evaluate(self, request: OpaInput) -> OpaResult:
        connection = None
        active_socket: socket.socket | None = None
        timer = None
        try:
            request = OpaInput.model_validate(request)
            body = json.dumps(
                {"input": request.model_dump(mode="json")}, ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
            if len(body) > 8192:
                raise OpaError()
            context, tls_trust = client_context()
            connection = http.client.HTTPSConnection(OPA_HOST, OPA_PORT, timeout=2, context=context)
            deadline = time.monotonic() + 2

            def abort() -> None:
                try:
                    current = active_socket or (connection.sock if connection is not None else None)
                    if current is not None:
                        current.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            timer = threading.Timer(2, abort)
            timer.daemon = True
            timer.start()
            connection.connect()
            active_socket = connection.sock
            if active_socket is None:
                raise OpaError()
            peer = active_socket.getpeercert(binary_form=True)
            if (
                type(peer) is not bytes
                or hashlib.sha256(peer).hexdigest() != tls_trust.server_sha256
            ):
                raise OpaError()
            connection.request(
                "POST", OPA_PATH, body=body, headers={"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            try:
                if response.status != 200:
                    raise OpaError()
                length = response.getheader("Content-Length")
                if length is not None and (not length.isdecimal() or int(length) > 8192):
                    raise OpaError()
                data = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise OpaError("unavailable")
                    # HTTP/1.0 or Connection: close detaches the socket while response.fp
                    # still owns the stream. Do not call settimeout on the closed wrapper.
                    if connection.sock is not None:
                        connection.sock.settimeout(remaining)
                    chunk = response.read1(min(4096, 8193 - len(data)))
                    data.extend(chunk)
                    if len(data) > 8192:
                        raise OpaError()
                    if not chunk:
                        break
                value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
                if not isinstance(value, dict) or set(value) != {"result"}:
                    raise OpaError()
                result = OpaResult.model_validate(value["result"])
                if any(
                    getattr(result, key) != getattr(request, key)
                    for key in ("policy_version", "bundle_hash", "catalog_hash", "input_hash")
                ):
                    raise OpaError()
                # Expiry or rotation while the PDP was answering cancels this result.
                if load_material().trust != tls_trust:
                    raise OpaError()
                return result
            finally:
                response.close()
        except (ConnectionError, TimeoutError, OSError):
            raise OpaError("unavailable") from None
        except OpaError:
            raise
        except Exception:
            raise OpaError() from None
        finally:
            if timer is not None:
                timer.cancel()
            if connection is not None:
                connection.close()
