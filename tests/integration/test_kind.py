"""Live read-only authorization tests, explicitly enabled for our local kind."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from aegisops.domain.models import PodSnapshot
from aegisops.sources.env_guard import load_reader_identity
from aegisops.sources.kubernetes_readonly import KubernetesReadOnlySource
from aegisops.sources.safe_client import ReadOnlyClient, ReadOnlyError

pytestmark = [pytest.mark.integration, pytest.mark.kind_live]
ROOT = Path(__file__).resolve().parents[2]


def test_live_collector_normalizes_actual_owners(raw_reader: Any) -> None:
    reader = ReadOnlyClient(load_reader_identity())
    try:
        pods = {pod.metadata.name: pod for pod in reader.list_pods().items}
        snapshots = KubernetesReadOnlySource(reader, include_logs=True).collect()
        assert len(snapshots) == 10
        assert len({snapshot.snapshot_id for snapshot in snapshots}) == 10
        for snapshot in snapshots:
            assert snapshot.origin.value == "local_kind"
            assert snapshot.namespace == "aegisops-demo"
            controller = next(
                ref for ref in pods[snapshot.pod_name].metadata.owner_references if ref.controller
            )
            assert snapshot.owner.kind == controller.kind == "ReplicaSet"
            assert snapshot.owner.name == controller.name
            assert len(snapshot.events) <= 64
            if snapshot.log_tail is not None:
                assert len(snapshot.log_tail.encode("utf-8")) <= 16384
                assert len(snapshot.log_tail.splitlines()) <= 100
        assert any(snapshot.events for snapshot in snapshots)
        assert any(snapshot.log_tail for snapshot in snapshots)
    finally:
        reader.close()


@pytest.fixture
def raw_reader() -> Any:
    if os.environ.get("AEGISOPS_KIND_LIVE") != "1":
        pytest.skip("live kind integration is explicitly disabled")
    identity = load_reader_identity()
    config = client.Configuration()
    config.host = identity.server
    config.verify_ssl = True
    config.ssl_ca_cert = str(identity.ca_file)
    config.api_key["authorization"] = "Bearer " + identity.token.get_secret_value()
    config.debug = False
    config.proxy = None
    config.retries = 0
    api = client.ApiClient(configuration=config)
    try:
        yield api
    finally:
        api.close()


def test_live_read_operations(raw_reader: Any) -> None:
    reader = ReadOnlyClient(load_reader_identity())
    try:
        reader.verify_environment()
        pods = reader.list_pods()
        assert len(pods.items) == 10
        assert len(reader.list_events().items) >= 1
        healthy = next(
            p for p in pods.items if p.metadata.labels.get("aegisops.io/scenario") == "neg-healthy"
        )
        assert isinstance(reader.read_logs(healthy.metadata.name), str)
    finally:
        reader.close()


@pytest.mark.parametrize(
    "method,path,body",
    [
        (
            "POST",
            "/api/v1/namespaces/aegisops-demo/pods",
            {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "forbidden"}},
        ),
        (
            "PATCH",
            "/api/v1/namespaces/aegisops-demo/pods/forbidden",
            {"metadata": {"labels": {"forbidden": "true"}}},
        ),
        ("DELETE", "/api/v1/namespaces/aegisops-demo/pods/forbidden", None),
        ("POST", "/api/v1/namespaces/aegisops-demo/pods/forbidden/exec", None),
        ("GET", "/api/v1/namespaces/aegisops-demo/pods/forbidden/exec", None),
        ("POST", "/api/v1/namespaces/aegisops-demo/pods/forbidden/attach", None),
        ("POST", "/api/v1/namespaces/aegisops-demo/pods/forbidden/portforward", None),
        ("GET", "/api/v1/namespaces/aegisops-demo/secrets", None),
        ("GET", "/api/v1/namespaces/aegisops-demo/configmaps", None),
        ("GET", "/apis/rbac.authorization.k8s.io/v1/namespaces/aegisops-demo/roles", None),
        ("GET", "/api/v1/namespaces/kube-system/pods", None),
        ("GET", "/api/v1/namespaces/kube-system", None),
        ("GET", "/api/v1/namespaces", None),
    ],
)
def test_real_rbac_denies_unsafe_requests(
    raw_reader: Any, method: str, path: str, body: object
) -> None:
    with pytest.raises(ApiException) as failure:
        raw_reader.call_api(
            path,
            method,
            body=body,
            auth_settings=["BearerToken"],
            header_params={
                "Content-Type": "application/merge-patch+json"
                if method == "PATCH"
                else "application/json"
            },
            _request_timeout=(3, 10),
            _return_http_data_only=True,
        )
    assert failure.value.status == 403


def test_exact_namespace_resource_permissions(raw_reader: Any) -> None:
    authorization = client.AuthorizationV1Api(raw_reader)
    review = authorization.create_self_subject_rules_review(
        client.V1SelfSubjectRulesReview(
            spec=client.V1SelfSubjectRulesReviewSpec(namespace="aegisops-demo")
        ),
        _request_timeout=(3, 10),
    )
    actual = []
    for rule in review.status.resource_rules:
        if rule.api_groups == [""]:
            actual.append(
                {
                    "verbs": sorted(rule.verbs),
                    "resources": sorted(rule.resources),
                    "resource_names": sorted(rule.resource_names or []),
                }
            )
    assert sorted(actual, key=lambda value: json.dumps(value, sort_keys=True)) == sorted(
        [
            {
                "verbs": ["get", "list", "watch"],
                "resources": ["events", "pods"],
                "resource_names": [],
            },
            {"verbs": ["get"], "resources": ["pods/log"], "resource_names": []},
            {"verbs": ["get"], "resources": ["namespaces"], "resource_names": ["aegisops-demo"]},
        ],
        key=lambda value: json.dumps(value, sort_keys=True),
    )
    assert not review.status.incomplete


def test_captured_snapshots_match_live_shape(raw_reader: Any) -> None:
    reader = ReadOnlyClient(load_reader_identity())
    try:
        pods = {p.metadata.labels.get("aegisops.io/scenario"): p for p in reader.list_pods().items}
        for directory in sorted((ROOT / "scenarios/local-kind").iterdir()):
            snapshot = PodSnapshot.model_validate_json(
                (directory / "snapshot.json").read_text(encoding="utf-8")
            )
            pod = pods[directory.name]
            assert snapshot.origin.value == "local_kind"
            assert snapshot.pod_name == pod.metadata.name
            assert [item.name for item in snapshot.containers] == [
                item.name for item in pod.status.container_statuses
            ]
            expected = yaml.safe_load((directory / "expected.yaml").read_text(encoding="utf-8"))
            assert snapshot.containers[0].waiting_reason == expected["expected_waiting_reason"]
    finally:
        reader.close()


def test_live_marker_removal_blocks_reads(raw_reader: Any) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "local_marker_admin", ROOT / "scripts/local_kind.py"
    )
    assert spec is not None and spec.loader is not None
    admin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(admin)
    admin.verify_cluster()
    reader = ReadOnlyClient(load_reader_identity())
    try:
        admin.kubectl(
            [
                "label",
                "namespace",
                "aegisops-demo",
                "aegisops.io/environment=invalid-marker",
                "--overwrite",
            ]
        )
        try:
            with pytest.raises(ReadOnlyError):
                reader.list_pods()
        finally:
            admin.kubectl(
                [
                    "label",
                    "namespace",
                    "aegisops-demo",
                    "aegisops.io/environment=local-demo",
                    "--overwrite",
                ]
            )
    finally:
        reader.close()
