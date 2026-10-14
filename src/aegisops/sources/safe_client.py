"""Read-only Kubernetes client for the local kind demo environment.

This module exposes a deliberately tiny surface: a handful of GET-only
operations against the ``aegisops-demo`` namespace.  It never loads a
kubeconfig from disk or environment, never performs writes, and rejects
any request that is not a whitelisted GET against the pinned origin.
"""

from __future__ import annotations

import re
from typing import Any, Final
from urllib.parse import urlsplit

from kubernetes import client as k8s_client
from kubernetes.client import ApiClient, Configuration
from kubernetes.client.rest import RESTClientObject

from aegisops.sources.env_guard import (
    ReaderIdentity,
    ensure_not_expired,
)

__all__ = ["ReadOnlyError", "LogUnavailableError", "ReadOnlyClient"]

_NAMESPACE: Final[str] = "aegisops-demo"
_NS_PATH: Final[str] = f"/api/v1/namespaces/{_NAMESPACE}"
_PODS_PATH: Final[str] = f"{_NS_PATH}/pods"
_EVENTS_PATH: Final[str] = f"{_NS_PATH}/events"
_LOG_RE: Final[re.Pattern[str]] = re.compile(
    rf"^{re.escape(_PODS_PATH)}/(?P<pod>[a-z0-9](?:[-.a-z0-9]{{0,251}}[a-z0-9])?)/log$"
)
_DNS_LABEL_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
_ALLOWED_PATHS: Final[frozenset[str]] = frozenset({_NS_PATH, _PODS_PATH, _EVENTS_PATH})
_FORBIDDEN_SUBSTRINGS: Final[tuple[str, ...]] = (
    "exec",
    "attach",
    "portforward",
    "port-forward",
)


class ReadOnlyError(RuntimeError):
    """Raised for any rejected or failed read-only operation."""


class LogUnavailableError(ReadOnlyError):
    """The pod/container has no available log stream (HTTP 400 or 404)."""


def _fail() -> ReadOnlyError:
    return ReadOnlyError("local kind read failed")


def _origin(server: str) -> tuple[str, str, int]:
    parts = urlsplit(server)
    if parts.scheme != "https":
        raise _fail()
    host = parts.hostname
    if host is None:
        raise _fail()
    try:
        port = parts.port
    except ValueError:
        raise _fail() from None
    if port is None:
        raise _fail()
    if not 1 <= port <= 65535 or parts.username or parts.password:
        raise _fail()
    return (parts.scheme, host, port)


def _check_loopback(server: str) -> None:
    scheme, host, _port = _origin(server)
    if scheme != "https":
        raise _fail()
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise _fail()
    parts = urlsplit(server)
    if parts.query or parts.fragment or parts.path not in {"", "/"}:
        raise _fail()


def _path_allowed(path: str) -> bool:
    if path in _ALLOWED_PATHS:
        return True
    return _LOG_RE.fullmatch(path) is not None


def _reject_forbidden(path: str) -> None:
    if not _path_allowed(path):
        raise _fail()


def _validate_pod_name(pod_name: str) -> None:
    if not isinstance(pod_name, str) or not pod_name:
        raise _fail()
    if len(pod_name) > 253:
        raise _fail()
    for label in pod_name.split("."):
        if not label or len(label) > 63:
            raise _fail()
        if _DNS_LABEL_RE.fullmatch(label) is None:
            raise _fail()


class _GuardedPoolManager:
    """Proxy around urllib3's PoolManager enforcing GET + pinned origin."""

    def __init__(self, pool: Any, origin: tuple[str, str, int], identity: ReaderIdentity) -> None:
        self._pool = pool
        self._origin = origin
        self._identity = identity

    def request(self, *args: Any, **kwargs: Any) -> Any:
        try:
            ensure_not_expired(self._identity)
        except ValueError:
            raise _fail() from None
        method = kwargs.get("method")
        url = kwargs.get("url")
        if method is None and args:
            method = args[0]
        if url is None and len(args) > 1:
            url = args[1]
        if not isinstance(method, str) or method.upper() != "GET":
            raise _fail()
        if not isinstance(url, str):
            raise _fail()
        parts = urlsplit(url)
        if parts.username or parts.password or parts.fragment:
            raise _fail()
        if _origin(url) != self._origin:
            raise _fail()
        if not _path_allowed(parts.path):
            raise _fail()
        _reject_forbidden(parts.path)
        kwargs["redirect"] = False
        return self._pool.request(*args, **kwargs)

    def clear(self) -> None:
        clear = getattr(self._pool, "clear", None)
        if callable(clear):
            clear()


class _GuardedApiClient(ApiClient):  # type: ignore[misc]  # upstream SDK has no type stubs
    """ApiClient subclass rejecting non-GET and non-whitelisted paths."""

    def __init__(self, configuration: Configuration, identity: ReaderIdentity) -> None:
        super().__init__(configuration=configuration)
        self._identity = identity

    def call_api(self, resource_path: str, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            ensure_not_expired(self._identity)
        except ValueError:
            raise _fail() from None
        if not isinstance(method, str) or method.upper() != "GET":
            raise _fail()
        if not isinstance(resource_path, str):
            raise _fail()
        parameters = kwargs.get("path_params", args[0] if args else {}) or {}
        if not isinstance(parameters, dict) or any(
            not isinstance(value, str) for value in parameters.values()
        ):
            raise _fail()
        try:
            rendered = resource_path.format_map(parameters)
        except (KeyError, ValueError):
            raise _fail() from None
        if not _path_allowed(rendered):
            raise _fail()
        return super().call_api(resource_path, method, *args, **kwargs)


class ReadOnlyClient:
    """GET-only client bound to a validated local kind reader identity."""

    def __init__(self, identity: ReaderIdentity) -> None:
        _check_loopback(identity.server)
        if identity.namespace != _NAMESPACE:
            raise _fail()
        ensure_not_expired(identity)
        self._identity = identity
        self._origin = _origin(identity.server)
        self._closed = False

        configuration = Configuration()
        configuration.host = identity.server
        configuration.verify_ssl = True
        configuration.ssl_ca_cert = str(identity.ca_file)
        configuration.api_key = {"authorization": "Bearer " + identity.token.get_secret_value()}
        configuration.debug = False
        configuration.proxy = None
        configuration.retries = 0

        self._api_client = _GuardedApiClient(configuration=configuration, identity=identity)
        self._install_guarded_pool()
        self._core = k8s_client.CoreV1Api(api_client=self._api_client)

    def _install_guarded_pool(self) -> None:
        rest: RESTClientObject = self._api_client.rest_client
        pool = getattr(rest, "pool_manager", None)
        if pool is None:
            raise _fail()
        rest.pool_manager = _GuardedPoolManager(pool, self._origin, self._identity)

    def _guard(self) -> None:
        if self._closed:
            raise _fail()
        try:
            ensure_not_expired(self._identity)
        except ValueError:
            raise _fail() from None

    def _verify_marker(self) -> None:
        ns = self._core.read_namespace(name=_NAMESPACE, _request_timeout=(3, 10))
        labels = getattr(getattr(ns, "metadata", None), "labels", None) or {}
        if labels.get("aegisops.io/environment") != "local-demo":
            raise _fail()
        if labels.get("aegisops.io/instance-id") != self._identity.instance_id:
            raise _fail()

    def verify_environment(self) -> None:
        self._guard()
        try:
            self._verify_marker()
        except ReadOnlyError:
            raise
        except Exception:
            raise _fail() from None
        return None

    def list_pods(self, label_selector: str = "", *, continue_token: str = "") -> Any:
        self._guard()
        if not isinstance(label_selector, str) or len(label_selector) > 256:
            raise _fail()
        self._check_continue(continue_token)
        try:
            self._verify_marker()
            return self._core.list_namespaced_pod(
                namespace=_NAMESPACE,
                label_selector=label_selector,
                limit=100,
                _continue=continue_token,
                _request_timeout=(3, 10),
            )
        except ReadOnlyError:
            raise
        except Exception:
            raise _fail() from None

    @staticmethod
    def _check_continue(value: str) -> None:
        if not isinstance(value, str) or len(value) > 4096:
            raise _fail()

    def list_events(self, *, pod_uid: str = "", continue_token: str = "") -> Any:
        self._guard()
        if not isinstance(pod_uid, str) or (
            pod_uid and not re.fullmatch(r"[a-zA-Z0-9-]{1,128}", pod_uid)
        ):
            raise _fail()
        self._check_continue(continue_token)
        try:
            self._verify_marker()
            return self._core.list_namespaced_event(
                namespace=_NAMESPACE,
                limit=64,
                field_selector=f"involvedObject.uid={pod_uid}" if pod_uid else "",
                _continue=continue_token,
                _request_timeout=(3, 10),
            )
        except ReadOnlyError:
            raise
        except Exception:
            raise _fail() from None

    def read_logs(
        self,
        pod_name: str,
        container: str = "app",
        previous: bool = False,
    ) -> str:
        self._guard()
        _validate_pod_name(pod_name)
        if not isinstance(container, str) or not _DNS_LABEL_RE.fullmatch(container):
            raise _fail()
        if type(previous) is not bool:
            raise _fail()
        try:
            self._verify_marker()
        except Exception:
            raise _fail() from None
        try:
            data = self._core.read_namespaced_pod_log(
                name=pod_name,
                namespace=_NAMESPACE,
                container=container,
                previous=previous,
                tail_lines=100,
                limit_bytes=16384,
                timestamps=False,
                _request_timeout=(3, 10),
            )
        except ReadOnlyError:
            raise
        except k8s_client.exceptions.ApiException as error:
            if error.status in {400, 404}:
                raise LogUnavailableError("local kind log unavailable") from None
            raise _fail() from None
        except Exception:
            raise _fail() from None
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        if isinstance(data, str):
            return data
        raise _fail()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            try:
                self._api_client.rest_client.pool_manager.clear()
            finally:
                self._api_client.close()
        except Exception:
            raise _fail() from None
