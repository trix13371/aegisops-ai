import copy

import pytest
from pydantic import ValidationError

from aegisops.domain.models import (
    DryRunResult,
    Evidence,
    PodSnapshot,
    Recommendation,
    RecommendationDraft,
)
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = pytest.mark.contract


@pytest.fixture
def pod_snapshot_seed() -> dict:
    data = ScenarioRepository().load("crashloop-exit-1")[1].model_dump(mode="json")
    data["events"] = [
        {
            "reason": "BackOff",
            "type": "Warning",
            "message": "Observed restart",
            "count": 1,
            "last_seen": data["observed_at"],
        }
    ]
    return data


def test_pod_snapshot_valid_seed_and_negatives(pod_snapshot_seed: dict) -> None:
    assert PodSnapshot.model_validate(pod_snapshot_seed)
    for mod in [
        lambda d: d.update(extra_canary="canary"),
        lambda d: d.update(origin="production"),
        lambda d: d.update(observed_at="2026-10-10 12:00:00"),
        lambda d: d["containers"].append(d["containers"][0]),
        lambda d: d.update(log_tail=("a\n" * 101)[:-1]),
        lambda d: d.update(log_tail="x" * 16385),
        lambda d: d["events"].extend([d["events"][0]] * 65),
        lambda d: d["containers"][0].update(is_init=1),
    ]:
        p = copy.deepcopy(pod_snapshot_seed)
        mod(p)
        with pytest.raises(ValidationError):
            PodSnapshot.model_validate(p)


def test_evidence_valid_and_negatives() -> None:
    seed = {
        "evidence_id": "ev-001",
        "source": "pod_status",
        "summary": "observed",
        "observed_at": "2026-10-10T12:00:00Z",
        "attributes": {},
    }
    ev = Evidence.model_validate(seed)
    assert ev.evidence_id == "ev-001"
    with pytest.raises(ValidationError):
        Evidence.model_validate({**seed, "attributes": {"nested": {"k": "v"}}})
    with pytest.raises(ValidationError):
        Evidence.model_validate({**seed, "attributes": {"k": float("nan")}})
    with pytest.raises(ValidationError):
        Evidence.model_validate({**seed, "attributes": {"k": "x" * 513}})
    with pytest.raises(TypeError):
        ev.attributes["k"] = "v"


def test_draft_and_recommendation_and_dryrun_negatives() -> None:
    draft_seed = {
        "title": "T",
        "rationale": "R",
        "proposed_action": {"action_id": "escalate_to_human", "params": {}},
    }
    assert RecommendationDraft.model_validate(draft_seed)
    with pytest.raises(ValidationError):
        RecommendationDraft.model_validate({**draft_seed, "execution_performed": False})
    rec_seed = {
        **draft_seed,
        "recommendation_id": "rec-1",
        "incident_id": "inc-1",
        "diagnosis_id": "diag-1",
        "reversibility_class": "R1",
        "requires_human_approval": True,
        "execution_performed": False,
    }
    assert Recommendation.model_validate(rec_seed)
    for bad in (1, 1.0, False):
        with pytest.raises(ValidationError):
            Recommendation.model_validate({**rec_seed, "requires_human_approval": bad})
    dr_seed = {
        "incident_id": "inc-1",
        "recommendation_id": "rec-1",
        "summary": "sim",
        "simulated": True,
        "execution_performed": False,
    }
    assert DryRunResult.model_validate(dr_seed)
    for mod in [{"execution_performed": True}, {"execution_performed": 0}, {"simulated": 1}]:
        with pytest.raises(ValidationError):
            DryRunResult.model_validate({**dr_seed, **mod})
