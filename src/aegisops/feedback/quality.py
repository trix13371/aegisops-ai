"""Validate a bounded set of current labels and count distinct incidents."""

from aegisops.audit.validation import sanitize_record
from aegisops.domain.models import AuditRecord, Origin
from aegisops.feedback.models import FeedbackError, FeedbackRecord, IncidentOutcome
from aegisops.feedback.quality_case import build_case
from aegisops.feedback.quality_models import ComparisonStatus, QualityReport
from aegisops.feedback.validation import recommendation_hash, sanitize_feedback


def compare_labels(
    pairs: tuple[tuple[FeedbackRecord, AuditRecord], ...],
    *,
    origin: Origin,
    limit: int,
    has_more: bool,
    incomplete_incidents: frozenset[str] = frozenset(),
) -> QualityReport:
    try:
        if (
            type(origin) is not Origin
            or type(limit) is not int
            or not 1 <= limit <= 100
            or type(has_more) is not bool
            or type(pairs) is not tuple
            or len(pairs) > limit
            or type(incomplete_incidents) is not frozenset
            or any(type(value) is not str for value in incomplete_incidents)
            or (incomplete_incidents and not has_more)
        ):
            raise FeedbackError()
        groups: dict[str, list[tuple[FeedbackRecord, AuditRecord]]] = {}
        validated: list[FeedbackRecord] = []
        for pair in pairs:
            if type(pair) is not tuple or len(pair) != 2:
                raise FeedbackError()
            feedback, audit = sanitize_feedback(pair[0]), sanitize_record(pair[1])
            if (
                audit.incident is None
                or audit.recommendation is None
                or feedback.run_id != audit.run_id
                or feedback.origin is not origin
                or audit.origin is not origin
                or feedback.recommendation_id != audit.recommendation.recommendation_id
                or feedback.recommendation_hash != recommendation_hash(audit.recommendation)
            ):
                raise FeedbackError()
            validated.append(feedback)
            groups.setdefault(audit.incident.incident_id, []).append((feedback, audit))
        identifiers = {record.feedback_id for record in validated}
        if (
            len(identifiers) != len(validated)
            or any(record.supersedes_id in identifiers for record in validated)
            or not incomplete_incidents.issubset(groups)
        ):
            raise FeedbackError()
        cases = tuple(
            build_case(tuple(groups[key]), incomplete=key in incomplete_incidents)
            for key in sorted(groups)
        )
        return QualityReport(
            origin=origin,
            limit=limit,
            sample_size=len(pairs),
            has_more=has_more,
            cases=cases,
            matches=sum(case.status is ComparisonStatus.MATCH for case in cases),
            mismatches=sum(case.status is ComparisonStatus.MISMATCH for case in cases),
            abstentions=sum(case.status is ComparisonStatus.ABSTAINED for case in cases),
            conflicts=sum(case.status is ComparisonStatus.CONFLICT for case in cases),
            unlabelled=sum(case.status is ComparisonStatus.UNLABELLED for case in cases),
            incomplete=sum(case.status is ComparisonStatus.SAMPLE_INCOMPLETE for case in cases),
            resolved_incidents=sum(
                case.incident_outcome is IncidentOutcome.RESOLVED for case in cases
            ),
            unresolved_incidents=sum(
                case.incident_outcome is IncidentOutcome.UNRESOLVED for case in cases
            ),
        )
    except Exception:
        raise FeedbackError() from None
