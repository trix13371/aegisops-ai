"""Developer-only local cluster operations. Never imported by src/aegisops."""

import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from aegisops.io import contained_file

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local" / "kind"
ADMIN = LOCAL / "admin-kubeconfig.yaml"
READER = LOCAL / "reader-kubeconfig.yaml"
STATE = LOCAL / "state.json"
NAME = "aegisops-demo"
CONTEXT = "kind-aegisops-demo"
NODE_IMAGE = (
    "kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed"
)
API_PORT = 16443


class LocalClusterError(ValueError):
    pass


def run(executable: str, arguments: list[str], input_text: str | None = None) -> str:
    environment = os.environ.copy()
    environment.pop("KUBECONFIG", None)
    environment["KIND_EXPERIMENTAL_PROVIDER"] = "docker"
    result = subprocess.run(
        [executable, *arguments],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
    )
    if result.returncode:
        # Do not echo kubectl diagnostics or certificate/token-bearing stdout.
        raise LocalClusterError("local cluster command failed") from None
    return result.stdout


def kind(arguments: list[str]) -> str:
    executable = os.environ.get("AEGISOPS_KIND_BIN") or shutil.which("kind")
    if not executable:
        raise LocalClusterError("install kind or set developer AEGISOPS_KIND_BIN")
    return run(executable, arguments)


def kubectl(arguments: list[str], input_text: str | None = None) -> str:
    executable = os.environ.get("AEGISOPS_KUBECTL_BIN") or shutil.which("kubectl")
    if not executable:
        raise LocalClusterError("install kubectl")
    admin_document()
    return run(
        executable, ["--kubeconfig", str(ADMIN), "--context", CONTEXT, *arguments], input_text
    )


def private_write(path: Path, value: str) -> None:
    if (
        path.is_symlink()
        or LOCAL.is_symlink()
        or (ROOT / ".local").is_symlink()
        or not LOCAL.resolve().is_relative_to(ROOT.resolve())
        or not path.resolve().is_relative_to(LOCAL.resolve())
    ):
        raise LocalClusterError("private destination outside project")
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(value)
    if os.name != "nt":
        path.chmod(0o600)


def admin_document() -> dict:
    try:
        checked = contained_file(ROOT, str(ADMIN.relative_to(ROOT)))
        document = yaml.safe_load(checked.read_text(encoding="utf-8"))
        if document["current-context"] != CONTEXT:
            raise LocalClusterError("foreign current-context refused")
        if len(document["contexts"]) != 1 or len(document["clusters"]) != 1:
            raise LocalClusterError("foreign kubeconfig refused")
        context = document["contexts"][0]
        if context["name"] != CONTEXT or context["context"]["cluster"] != CONTEXT:
            raise LocalClusterError("foreign kubeconfig refused")
        if context["context"].get("user") != CONTEXT or len(document.get("users", [])) != 1:
            raise LocalClusterError("foreign administrative identity refused")
        identity = document["users"][0]
        if identity["name"] != CONTEXT or set(identity["user"]) != {
            "client-certificate-data",
            "client-key-data",
        }:
            raise LocalClusterError("administrative auth plugins refused")
        cluster = document["clusters"][0]
        if cluster["name"] != CONTEXT:
            raise LocalClusterError("foreign kubeconfig refused")
        endpoint = urlsplit(cluster["cluster"]["server"])
        if (
            endpoint.scheme != "https"
            or endpoint.hostname not in {"127.0.0.1", "::1"}
            or endpoint.port is None
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or endpoint.path not in {"", "/"}
            or "insecure-skip-tls-verify" in cluster["cluster"]
            or "proxy-url" in cluster["cluster"]
            or set(cluster["cluster"]) != {"server", "certificate-authority-data"}
        ):
            raise LocalClusterError("non-local or unsafe endpoint refused")
        return document
    except (OSError, ValueError, KeyError, TypeError):
        raise LocalClusterError("local administrative configuration rejected") from None


def verify_cluster(check_namespace: bool = True) -> dict:
    document = admin_document()
    try:
        checked_state = contained_file(ROOT, str(STATE.relative_to(ROOT)))
        state = json.loads(checked_state.read_text(encoding="utf-8"))
        cluster = document["clusters"][0]["cluster"]
        authority = base64.b64decode(cluster["certificate-authority-data"], validate=True)
        if (
            state["cluster_name"] != NAME
            or state["context"] != CONTEXT
            or state["namespace"] != NAME
            or state["node_image"] != NODE_IMAGE
            or state["server"] != cluster["server"]
            or state["ca_sha256"] != hashlib.sha256(authority).hexdigest()
        ):
            raise LocalClusterError("cluster registration mismatch")
        labels = json.loads(
            run(
                "docker",
                ["inspect", NAME + "-control-plane", "--format", "{{json .Config.Labels}}"],
            )
        )
        if labels.get("io.x-k8s.kind.cluster") != NAME:
            raise LocalClusterError("foreign container refused")
        if check_namespace:
            namespace = json.loads(kubectl(["get", "namespace", NAME, "-o", "json"]))
            marker = namespace["metadata"].get("labels", {})
            if (
                marker.get("aegisops.io/environment") != "local-demo"
                or marker.get("aegisops.io/instance-id") != state["instance_id"]
            ):
                raise LocalClusterError("namespace marker mismatch")
        return state
    except (OSError, ValueError, KeyError, TypeError):
        raise LocalClusterError("local cluster registration rejected") from None


def refresh_reader() -> None:
    verify_cluster()
    document = admin_document()
    token = kubectl(["create", "token", "aegisops-reader", "-n", NAME, "--duration=10m"]).strip()
    reader = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": CONTEXT,
        "clusters": [{"name": CONTEXT, "cluster": document["clusters"][0]["cluster"]}],
        "contexts": [
            {
                "name": CONTEXT,
                "context": {
                    "cluster": CONTEXT,
                    "user": "aegisops-reader",
                    "namespace": NAME,
                },
            }
        ],
        "users": [{"name": "aegisops-reader", "user": {"token": token}}],
    }
    private_write(READER, yaml.safe_dump(reader, sort_keys=False))
    print("Reader kubeconfig refreshed (requested TTL 10 minutes); credentials hidden.")


def cluster_up() -> None:
    LOCAL.mkdir(parents=True, exist_ok=True)
    existing = kind(["get", "clusters"]).splitlines()
    if NAME in existing:
        verify_cluster()
        refresh_reader()
        print("Existing owned local cluster reused.")
        return
    if ADMIN.exists() or STATE.exists():
        raise LocalClusterError("stale registration exists; inspect before recreating")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", API_PORT))
    except OSError:
        raise LocalClusterError(
            "local API port 16443 is unavailable or reserved by Windows"
        ) from None
    kind(
        [
            "create",
            "cluster",
            "--name",
            NAME,
            "--kubeconfig",
            str(ADMIN),
            "--config",
            str(ROOT / "deploy/local/kind.yaml"),
            "--wait",
            "180s",
        ]
    )
    document = admin_document()
    cluster = document["clusters"][0]["cluster"]
    authority = base64.b64decode(cluster["certificate-authority-data"], validate=True)
    state = {
        "cluster_name": NAME,
        "context": CONTEXT,
        "namespace": NAME,
        "instance_id": str(uuid.uuid4()),
        "server": cluster["server"],
        "ca_sha256": hashlib.sha256(authority).hexdigest(),
        "node_image": NODE_IMAGE,
    }
    private_write(STATE, json.dumps(state, indent=2) + "\n")
    private_write(LOCAL / "ca.crt", authority.decode("ascii"))
    verify_cluster(check_namespace=False)
    manifest = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": NAME,
            "labels": {
                "aegisops.io/environment": "local-demo",
                "aegisops.io/instance-id": state["instance_id"],
                "pod-security.kubernetes.io/enforce": "restricted",
            },
        },
    }
    kubectl(["apply", "-f", "-"], json.dumps(manifest))
    verify_cluster()
    kubectl(["apply", "-f", str(ROOT / "deploy/local/reader-rbac.yaml")])
    refresh_reader()
    print("Owned local kind cluster ready. Default kubeconfig was not used.")


def cluster_down() -> None:
    # Namespace availability is not required; context, CA and Docker ownership still are.
    verify_cluster(check_namespace=False)
    kind(["delete", "cluster", "--name", NAME, "--kubeconfig", str(ADMIN)])
    for path in (ADMIN, READER, STATE, LOCAL / "ca.crt"):
        if path.resolve().is_relative_to(LOCAL.resolve()) and path.exists():
            path.unlink()
    print("Only the owned aegisops-demo cluster was removed.")
