"""No request reaches urllib3 for a forbidden method, origin, path or expired identity."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException
from pydantic import SecretStr

from aegisops.sources.env_guard import ReaderIdentity
from aegisops.sources.safe_client import (
    LogUnavailableError,
    ReadOnlyClient,
    ReadOnlyError,
    _GuardedPoolManager,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def identity(tmp_path: Path) -> ReaderIdentity:
    return ReaderIdentity(
        "https://127.0.0.1:6443",
        SecretStr("synthetic-only"),
        tmp_path / "ca.crt",
        "aegisops-demo",
        "12345678-1234-1234-1234-123456789abc",
        datetime.now(UTC) + timedelta(minutes=10),
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/namespaces/aegisops-demo/pods"),
        ("PATCH", "/api/v1/namespaces/aegisops-demo/pods"),
        ("DELETE", "/api/v1/namespaces/aegisops-demo/pods"),
        ("GET", "/api/v1/namespaces/aegisops-demo/pods/demo/exec"),
        ("GET", "/api/v1/namespaces/aegisops-demo/pods/demo/attach"),
        ("GET", "/api/v1/namespaces/aegisops-demo/pods/demo/portforward"),
        ("GET", "/api/v1/namespaces/kube-system/pods"),
        ("GET", "/api/v1/namespaces/aegisops-demo/secrets"),
    ],
)
def test_transport_rejects_before_network(identity: ReaderIdentity, method: str, path: str) -> None:
    pool = MagicMock()
    guarded = _GuardedPoolManager(pool, ("https", "127.0.0.1", 6443), identity)
    with pytest.raises(ReadOnlyError):
        guarded.request(method, identity.server + path)
    pool.request.assert_not_called()


def test_redirect_and_remote_origin(identity: ReaderIdentity) -> None:
    pool = MagicMock()
    guarded = _GuardedPoolManager(pool, ("https", "127.0.0.1", 6443), identity)
    guarded.request("GET", identity.server + "/api/v1/namespaces/aegisops-demo/pods")
    assert pool.request.call_args.kwargs["redirect"] is False
    pool.reset_mock()
    with pytest.raises(ReadOnlyError):
        guarded.request("GET", "https://foreign.invalid:6443/api/v1/namespaces/aegisops-demo/pods")
    pool.request.assert_not_called()


def test_expired_token_stops_even_direct_transport(identity: ReaderIdentity) -> None:
    pool = MagicMock()
    expired = replace(identity, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    guarded = _GuardedPoolManager(pool, ("https", "127.0.0.1", 6443), expired)
    with pytest.raises(ReadOnlyError):
        guarded.request("GET", identity.server + "/api/v1/namespaces/aegisops-demo/pods")
    pool.request.assert_not_called()


def test_marker_checked_before_every_read(identity: ReaderIdentity) -> None:
    reader = ReadOnlyClient(identity)
    fake = MagicMock()
    fake.read_namespace.return_value = SimpleNamespace(
        metadata=SimpleNamespace(
            labels={
                "aegisops.io/environment": "local-demo",
                "aegisops.io/instance-id": identity.instance_id,
            }
        )
    )
    reader._core = fake
    try:
        reader.list_pods()
        assert fake.list_namespaced_pod.call_count == 1
        fake.read_namespace.return_value.metadata.labels = {}
        with pytest.raises(ReadOnlyError):
            reader.list_pods()
        assert fake.list_namespaced_pod.call_count == 1
        with pytest.raises(ReadOnlyError):
            reader.read_logs("../escape")
        with pytest.raises(ReadOnlyError):
            reader.read_logs("demo", container="app;cmd")
    finally:
        reader.close()


def test_template_path_and_nonget_api_guard(identity: ReaderIdentity) -> None:
    reader = ReadOnlyClient(identity)
    try:
        with pytest.raises(ReadOnlyError):
            reader._api_client.call_api(
                "/api/v1/namespaces/{namespace}/pods", "POST", {"namespace": "aegisops-demo"}
            )
        with pytest.raises(ReadOnlyError):
            reader._api_client.call_api(
                "/api/v1/namespaces/{namespace}/pods", "GET", {"namespace": "kube-system"}
            )
    finally:
        reader.close()


def guarded_fake(identity: ReaderIdentity) -> tuple[ReadOnlyClient, MagicMock]:
    reader = ReadOnlyClient(identity)
    fake = MagicMock()
    fake.read_namespace.return_value = SimpleNamespace(
        metadata=SimpleNamespace(
            labels={
                "aegisops.io/environment": "local-demo",
                "aegisops.io/instance-id": identity.instance_id,
            }
        )
    )
    reader._core = fake
    return reader, fake


def test_pagination_and_uid_filter_remain_guarded(identity: ReaderIdentity) -> None:
    reader, fake = guarded_fake(identity)
    try:
        reader.list_pods(continue_token="pod-page")  # noqa: S106 - pagination cursor
        assert fake.list_namespaced_pod.call_args.kwargs["_continue"] == "pod-page"
        reader.list_events(pod_uid="pod-123", continue_token="event-page")  # noqa: S106
        args = fake.list_namespaced_event.call_args.kwargs
        assert args["namespace"] == "aegisops-demo"
        assert args["field_selector"] == "involvedObject.uid=pod-123"
        assert args["_continue"] == "event-page"
        fake.read_namespace.return_value.metadata.labels = {}
        with pytest.raises(ReadOnlyError):
            reader.list_events(pod_uid="pod-123", continue_token="next")  # noqa: S106
        assert fake.list_namespaced_event.call_count == 1
        with pytest.raises(ReadOnlyError):
            reader.list_events(pod_uid="pod-123,metadata.namespace=kube-system")
        with pytest.raises(ReadOnlyError):
            reader.list_pods(continue_token="x" * 4097)
        assert fake.list_namespaced_pod.call_count == 1
    finally:
        reader.close()


@pytest.mark.parametrize("status", [400, 404, 401, 403, 500])
def test_only_missing_logs_are_optional(identity: ReaderIdentity, status: int) -> None:
    reader, fake = guarded_fake(identity)
    fake.read_namespaced_pod_log.side_effect = ApiException(status=status, reason="fake-canary")
    try:
        with pytest.raises(ReadOnlyError) as failure:
            reader.read_logs("demo")
        assert isinstance(failure.value, LogUnavailableError) == (status in {400, 404})
        assert "fake-canary" not in str(failure.value)
        fake.read_namespace.side_effect = ApiException(status=404, reason="marker-canary")
        with pytest.raises(ReadOnlyError) as marker_failure:
            reader.read_logs("demo")
        assert not isinstance(marker_failure.value, LogUnavailableError)
        assert fake.read_namespaced_pod_log.call_count == 1
    finally:
        reader.close()
