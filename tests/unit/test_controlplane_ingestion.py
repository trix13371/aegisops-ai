"""Restart/replay, partial failures and durable provenance with no external services."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from aegisops.audit.base import AuditStoreError
from aegisops.authorization.pep import DryRunPEP
from aegisops.controlplane.service import IngestionService
from aegisops.domain.authorization import OpaResult
from aegisops.domain.models import PodSnapshot
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import UnavailableLLMClient
from aegisops.telemetry.contracts import TelemetryError

pytestmark = [pytest.mark.unit, pytest.mark.safety]


class MemoryStore:
    def __init__(self, fail_at=None):
        self.rows = {}
        self.fail_at = fail_at

    def append(self, record):
        if self.fail_at is not None and len(self.rows) == self.fail_at:
            raise AuditStoreError()
        self.rows[record.run_id] = record

    def show(self, run_id):
        return self.rows[run_id]


class Counter(UnavailableLLMClient):
    def __init__(self):
        self.calls = 0

    def generate_structured(self, request, limits):
        self.calls += 1
        return super().generate_structured(request, limits)


class PDP:
    def evaluate(self, request):
        return OpaResult(
            **{
                name: getattr(request, name)
                for name in ("policy_version", "bundle_hash", "catalog_hash", "input_hash")
            },
            allow=True,
            autonomy_level="L0",
            execution_performed=False,
        )


@pytest.fixture
def consumer_factory(telemetry_case, open_control, valid_policy_reader):
    def create(store, *, blocked=False, clock=None):
        client = Counter()
        control = open_control.model_copy(update={"global_blocked": blocked})
        service = IngestionService(
            telemetry_case.root,
            AnalysisEngine(client),
            store,
            store,
            pep=DryRunPEP(
                PDP(), control_reader=lambda: control, release_reader=valid_policy_reader
            ),
            clock=clock or (lambda: telemetry_case.now),
        )
        return service, client

    return create


@pytest.mark.parametrize("blocked", [False, True])
def test_processing_and_cached_replay_after_restart_and_expiry(
    telemetry_case, consumer_factory, blocked
):
    case = telemetry_case
    store = MemoryStore()
    packet = case.make()
    first, client = consumer_factory(store, blocked=blocked)
    delivery = first.consume(packet)
    assert delivery.replayed is False and delivery.receipt.status == (
        "blocked" if blocked else "completed"
    )
    assert client.calls > 0 and len(store.rows) == 1
    assert next(iter(store.rows.values())).telemetry == delivery.receipt.telemetry
    second, second_client = consumer_factory(store, clock=lambda: case.now + timedelta(days=1))
    replay = second.consume(packet)
    assert replay.replayed is True and replay.receipt == delivery.receipt
    assert second_client.calls == 0 and len(store.rows) == 1


def test_empty_scan_also_has_provenance_and_one_audit(telemetry_case, consumer_factory):
    service, client = consumer_factory(MemoryStore())
    delivery = service.consume(telemetry_case.make(snapshots=()))
    assert delivery.receipt.snapshot_count == delivery.receipt.incident_count == 0
    assert len(delivery.receipt.audit_run_ids) == 1 and client.calls == 0


def test_same_id_with_other_signed_content_is_not_a_cached_success(
    telemetry_case, consumer_factory
):
    store = MemoryStore()
    service, client = consumer_factory(store)
    packet = telemetry_case.make()
    service.consume(packet)
    calls = client.calls
    other = telemetry_case.make(batch_id=packet.body.batch_id, snapshots=())
    with pytest.raises(TelemetryError):
        service.consume(other)
    assert client.calls == calls and len(store.rows) == 1


def test_expired_new_batch_is_not_claimed_or_analyzed(telemetry_case, consumer_factory):
    case = telemetry_case
    service, client = consumer_factory(MemoryStore(), clock=lambda: case.now + timedelta(minutes=5))
    with pytest.raises(TelemetryError):
        service.consume(case.make())
    assert client.calls == 0 and not (case.root / ".local/control-plane/inbox").exists()


@pytest.mark.parametrize(
    "failure", ["first_insert", "partial_insert", "receipt_write", "partial_receipt_write"]
)
def test_uncertain_attempt_is_not_retried(telemetry_case, consumer_factory, monkeypatch, failure):
    import aegisops.controlplane.service as module

    case = telemetry_case
    store = MemoryStore(
        fail_at=0 if failure == "first_insert" else 1 if failure == "partial_insert" else None
    )
    snapshots = (case.snapshot,)
    if failure == "partial_insert":
        other = case.snapshot.model_dump()
        other.update(
            snapshot_id="other-snapshot",
            pod_name="other-pod",
            owner={"kind": "Deployment", "name": "other"},
        )
        snapshots += (PodSnapshot.model_validate(other),)
    packet = case.make(snapshots=snapshots)
    if failure in {"receipt_write", "partial_receipt_write"}:
        original = module.write_new

        def reject(path, contents):
            if path.name.endswith(".receipt.json"):
                if failure == "partial_receipt_write":
                    original(path, b"{")
                raise OSError("not for output")
            original(path, contents)

        monkeypatch.setattr(module, "write_new", reject)
    first, _ = consumer_factory(store)
    with pytest.raises(TelemetryError):
        first.consume(packet)
    second, client = consumer_factory(store)
    with pytest.raises(TelemetryError):
        second.consume(packet)
    assert client.calls == 0
    assert len(store.rows) == (0 if failure == "first_insert" else 1)
    state = case.root / ".local/control-plane/inbox"
    assert (state / f"{packet.body.batch_id}.claim.json").exists()
    assert (state / f"{packet.body.batch_id}.receipt.json").exists() is (
        failure == "partial_receipt_write"
    )


def test_concurrent_consumers_only_one_can_claim(telemetry_case, consumer_factory, monkeypatch):
    import aegisops.controlplane.service as module

    store = MemoryStore()
    packet = telemetry_case.make()
    original = module.write_new
    rendezvous = Barrier(2)

    def race(path, contents):
        if path.name.endswith(".claim.json"):
            rendezvous.wait(timeout=10)
        original(path, contents)

    monkeypatch.setattr(module, "write_new", race)

    def consume():
        service, client = consumer_factory(store)
        try:
            return service.consume(packet), client.calls
        except TelemetryError:
            return None, client.calls

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))
    assert sum(delivery is not None for delivery, _ in results) == 1
    assert all(calls == 0 for delivery, calls in results if delivery is None)
    assert all(calls > 0 for delivery, calls in results if delivery is not None)
    assert len(store.rows) == 1


def test_cached_receipt_requires_existing_matching_database_record(
    telemetry_case, consumer_factory
):
    store = MemoryStore()
    service, _ = consumer_factory(store)
    packet = telemetry_case.make()
    service.consume(packet)
    store.rows.clear()
    second, client = consumer_factory(store)
    with pytest.raises(TelemetryError):
        second.consume(packet)
    assert client.calls == 0


def test_new_receipt_requires_exact_readback(telemetry_case, consumer_factory):
    class ChangedReadback(MemoryStore):
        def show(self, run_id):
            record = super().show(run_id)
            return record.model_copy(
                update={"completed_at": record.completed_at + timedelta(seconds=1)}
            )

    store = ChangedReadback()
    service, _ = consumer_factory(store)
    packet = telemetry_case.make()
    with pytest.raises(TelemetryError):
        service.consume(packet)
    state = telemetry_case.root / ".local/control-plane/inbox"
    assert len(store.rows) == 1
    assert not (state / f"{packet.body.batch_id}.receipt.json").exists()
