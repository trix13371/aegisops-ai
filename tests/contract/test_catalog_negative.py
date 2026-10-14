import json
import traceback

import pytest
from pydantic import ValidationError

from aegisops.actions.catalog import CatalogError, load_catalog
from aegisops.domain.models import ActionID, ActionProposal

pytestmark = pytest.mark.contract


def test_catalog_validate_proposal_negatives() -> None:
    catalog = load_catalog()
    valid_escalate = ActionProposal(action_id=ActionID.ESCALATE_TO_HUMAN, params={})
    assert catalog.validate_proposal(valid_escalate).action_id == ActionID.ESCALATE_TO_HUMAN
    with pytest.raises(ValidationError):
        ActionProposal(action_id="unknown_action", params={})
    invalid_proposals = [
        ActionProposal(action_id=ActionID.ESCALATE_TO_HUMAN, params={"extra": "val"}),
        ActionProposal(action_id=ActionID.RESTART_WORKLOAD, params={"namespace": "aegisops-demo"}),
        ActionProposal(
            action_id=ActionID.RESTART_WORKLOAD, params={"namespace": "prod", "workload": "api"}
        ),
        ActionProposal(action_id=ActionID.COLLECT_MORE_EVIDENCE, params={"scope": "env"}),
        ActionProposal(
            action_id=ActionID.RESTART_WORKLOAD,
            params={"namespace": "aegisops-demo", "workload": "bad;rm -rf"},
        ),
        ActionProposal(
            action_id=ActionID.RESTART_WORKLOAD,
            params={"namespace": "aegisops-demo", "workload": "a" * 64},
        ),
    ]
    canary = "fakecanarysecret12345"
    for p in invalid_proposals:
        with pytest.raises(CatalogError) as exc_info:
            catalog.validate_proposal(p)
        tb_text = "".join(traceback.format_exception(exc_info.value))
        assert canary not in str(exc_info.value) and canary not in tb_text
    constructed = ActionProposal.model_construct(
        action_id=ActionID.ESCALATE_TO_HUMAN, params={"unexpected": canary}
    )
    with pytest.raises(CatalogError):
        catalog.validate_proposal(constructed)
    with pytest.raises(TypeError):
        catalog.actions[0].params["k"] = None  # type: ignore


def test_catalog_file_load_negatives(tmp_path) -> None:
    base = load_catalog().model_dump(mode="json")

    def mutate_and_test(mutator) -> None:
        data = json.loads(json.dumps(base))
        mutator(data)
        f = tmp_path / "catalog.json"
        f.write_text(json.dumps(data))
        with pytest.raises(CatalogError):
            load_catalog(f)

    mutate_and_test(lambda d: d["actions"].append(d["actions"][0]))
    mutate_and_test(lambda d: d["actions"][0].update(reversibility="R3"))
    mutate_and_test(lambda d: d.update(extra_field="forbidden"))
