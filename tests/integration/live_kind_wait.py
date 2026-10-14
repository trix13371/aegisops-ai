"""Bounded polling: workload status can transition while the node recovers."""

import time

import pytest

from aegisops.detection.engine import DetectionEngine
from aegisops.sources.kubernetes_readonly import KubernetesReadOnlySource


def wait_for_crashing_snapshots(reader, *, minimum=4, timeout=180):
    deadline = time.monotonic() + timeout
    observed = {}
    while True:
        snapshots = KubernetesReadOnlySource(reader, include_logs=True).collect()
        # Scenarios restart independently: require distinct real CrashLoop observations
        # within the bounded window, rather than simultaneous transient status fields.
        for snapshot in snapshots:
            if DetectionEngine().detect([snapshot]):
                observed[(snapshot.owner.kind, snapshot.owner.name)] = snapshot
        if len(observed) >= minimum:
            return list(observed.values())
        if time.monotonic() >= deadline:
            pytest.fail("owned kind did not expose the expected CrashLoop states before timeout")
        time.sleep(min(2, max(0, deadline - time.monotonic())))
