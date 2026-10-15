"""Build the minimal PDP projection and verify its binding to the local decision."""

from aegisops.authorization.bundle import AuthorizationError, BundleConfig
from aegisops.domain.authorization import OpaInput, PepReceipt
from aegisops.domain.models import EvidenceSource, Incident, PolicyDecision, Recommendation


def build_input(
    incident: Incident,
    recommendation: Recommendation,
    decision: PolicyDecision,
    config: BundleConfig,
) -> OpaInput:
    try:
        incident = Incident.model_validate(incident)
        recommendation = Recommendation.model_validate(recommendation)
        decision = PolicyDecision.model_validate(decision)
        config = BundleConfig.model_validate(config)
        if decision.input_hash is None:
            raise AuthorizationError()
        kinds = {
            item.attributes.get("owner_kind")
            for item in incident.evidence
            if item.source is EvidenceSource.POD_STATUS
        }
        owners = tuple(sorted(value if type(value) is str else "__unknown__" for value in kinds))
        return OpaInput(
            bundle_hash=config.bundle_hash,
            catalog_hash=config.catalog_hash,
            input_hash=decision.input_hash,
            cluster_id=incident.cluster_id,
            incident_id=incident.incident_id,
            recommendation_id=recommendation.recommendation_id,
            recommendation_incident_id=recommendation.incident_id,
            namespace=incident.namespace,
            workload=incident.workload_name,
            action_id=recommendation.proposed_action.action_id.value,
            params=recommendation.proposed_action.params,
            reversibility=recommendation.reversibility_class.value,
            owner_kinds=owners,
        )
    except Exception:
        raise AuthorizationError() from None


def validate_receipt(
    incident: Incident,
    recommendation: Recommendation,
    decision: PolicyDecision,
    receipt: PepReceipt,
) -> PepReceipt:
    try:
        receipt = PepReceipt.model_validate(receipt)
        config = BundleConfig(
            policy_version=receipt.request.policy_version,
            bundle_hash=receipt.request.bundle_hash,
            catalog_hash=receipt.request.catalog_hash,
        )
        if receipt.request != build_input(incident, recommendation, decision, config):
            raise AuthorizationError()
        if receipt.python_allowed is not decision.allowed_for_dry_run:
            raise AuthorizationError()
        return receipt
    except Exception:
        raise AuthorizationError() from None
