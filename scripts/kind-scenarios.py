"""Apply/capture only owned local scenarios. Normalization is a fixture tool, not S0-04."""

import argparse
import json
import time
from datetime import UTC, datetime

import yaml
from local_kind import ROOT, LocalClusterError, kubectl, refresh_reader, verify_cluster

from aegisops.domain.models import PodSnapshot
from aegisops.sources.env_guard import load_reader_identity
from aegisops.sources.safe_client import ReadOnlyClient, ReadOnlyError


def apply_scenarios() -> None:
    verify_cluster()
    paths = sorted((ROOT / "scenarios/local-kind").glob("*/manifest.yaml"))
    if len(paths) != 10:
        raise LocalClusterError("expected ten local scenarios")
    for path in paths:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        if (
            manifest.get("kind") != "Deployment"
            or manifest["metadata"].get("namespace") != "aegisops-demo"
        ):
            raise LocalClusterError("foreign manifest refused")
        if manifest["metadata"].get("name") != path.parent.name:
            raise LocalClusterError("scenario name mismatch")
        kubectl(["apply", "-f", str(path)])
    print("Ten owned local scenario Deployments applied.")


def capture_scenarios(timeout: int) -> None:
    registration = verify_cluster()
    refresh_reader()
    client = ReadOnlyClient(load_reader_identity())
    expected = {
        path.parent.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in (ROOT / "scenarios/local-kind").glob("*/expected.yaml")
    }
    seen = {identifier: [] for identifier in expected}
    snapshots = {}
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline and len(snapshots) < len(expected):
            pods = client.list_pods().items
            for pod in pods:
                identifier = (pod.metadata.labels or {}).get("aegisops.io/scenario")
                if identifier not in expected or identifier in snapshots:
                    continue
                statuses = pod.status.container_statuses or []
                if not statuses:
                    continue
                status = statuses[0]
                waiting = status.state.waiting
                reason = waiting.reason if waiting else None
                if reason not in seen[identifier]:
                    seen[identifier].append(reason)
                target = expected[identifier]["expected_waiting_reason"]
                if reason != target:
                    continue
                if target is None and (not status.ready or status.state.running is None):
                    continue
                if identifier == "neg-old-restarts" and status.restart_count < 1:
                    continue
                terminated = status.last_state.terminated or status.state.terminated
                if identifier == "crashloop-oom" and (
                    not terminated or terminated.reason != "OOMKilled"
                ):
                    continue
                log = None
                if reason not in {"ImagePullBackOff", "CreateContainerConfigError"}:
                    try:
                        log = client.read_logs(pod.metadata.name, previous=bool(terminated))
                    except ReadOnlyError:
                        pass
                snapshot = PodSnapshot.model_validate(
                    {
                        "snapshot_id": "captured-" + identifier,
                        "origin": "local_kind",
                        "cluster_id": "kind-aegisops-demo",
                        "namespace": "aegisops-demo",
                        "pod_name": pod.metadata.name,
                        "owner": {"kind": "Deployment", "name": identifier},
                        "containers": [
                            {
                                "name": status.name,
                                "is_init": False,
                                "waiting_reason": reason,
                                "last_terminated_reason": terminated.reason if terminated else None,
                                "last_exit_code": terminated.exit_code if terminated else None,
                                "restart_count": status.restart_count,
                            }
                        ],
                        "events": [],
                        "log_tail": log,
                        "observed_at": datetime.now(UTC),
                    }
                )
                directory = ROOT / "scenarios/local-kind" / identifier
                (directory / "snapshot.json").write_text(
                    snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8"
                )
                snapshots[identifier] = {
                    "waiting_reason": reason,
                    "last_exit_code": terminated.exit_code if terminated else None,
                    "last_terminated_reason": terminated.reason if terminated else None,
                    "restart_count": status.restart_count,
                    "sequence_seen": seen[identifier],
                }
                print("Captured " + identifier, flush=True)
            if len(snapshots) < len(expected):
                time.sleep(0.25)
    finally:
        client.close()
    report = {
        "captured": snapshots,
        "missing": sorted(set(expected) - set(snapshots)),
        "observed_sequences": seen,
        "timeout_seconds": timeout,
        "instance_id": registration["instance_id"],
        "node_image": registration["node_image"],
    }
    destination = ROOT / ".local/kind/capture-report.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["missing"]:
        raise SystemExit("Some scenario statuses were not observed; see local capture report")
    print("Ten live local snapshots captured; no raw pod specs or credentials stored.")


parser = argparse.ArgumentParser()
parser.add_argument("operation", choices=("apply", "capture"))
parser.add_argument("--timeout", type=int, default=180)
arguments = parser.parse_args()
if not 30 <= arguments.timeout <= 300:
    raise SystemExit("Timeout must be 30..300 seconds")
try:
    if arguments.operation == "apply":
        apply_scenarios()
    else:
        capture_scenarios(arguments.timeout)
except (LocalClusterError, ReadOnlyError, ValueError):
    raise SystemExit("Local scenario operation failed; credentials hidden") from None
