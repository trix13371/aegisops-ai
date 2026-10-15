"""Append-only assessments; database constraints serialize competing corrections."""

from types import TracebackType
from uuid import UUID, uuid4

from sqlalchemy import exists, insert, select
from sqlalchemy.orm import aliased

from aegisops.audit.postgres import create_audit_engine
from aegisops.audit.reader import read_audit_record
from aegisops.db.models import RecommendationFeedback
from aegisops.domain.common import utc_now
from aegisops.domain.models import Origin
from aegisops.feedback.models import Assessment, FeedbackError, FeedbackRecord, FeedbackSummary
from aegisops.feedback.validation import recommendation_hash, sanitize_feedback


def _limit(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 100:
        raise FeedbackError()


class PostgresFeedbackStore:
    def __init__(self, database_url: str | None = None) -> None:
        try:
            self._engine = create_audit_engine(database_url)
        except Exception:
            raise FeedbackError() from None

    def add(
        self,
        run_id: UUID,
        *,
        reviewer: str,
        assessment: Assessment,
        comment: str = "",
        supersedes_id: UUID | None = None,
    ) -> FeedbackRecord:
        try:
            if type(run_id) is not UUID or (
                supersedes_id is not None and type(supersedes_id) is not UUID
            ):
                raise FeedbackError()
            # Validate the bounded user fields before any query or driver call.
            values = dict(
                feedback_id=uuid4(),
                run_id=run_id,
                reviewer=reviewer,
                assessment=assessment,
                comment=comment,
                supersedes_id=supersedes_id,
                created_at=utc_now(),
            )
            sanitize_feedback(
                dict(
                    **values,
                    origin=Origin.SYNTHETIC_FIXTURE,
                    recommendation_id="preflight",
                    recommendation_hash="0" * 64,
                )
            )
            with self._engine.begin() as connection:
                target = read_audit_record(connection, run_id)
                if target.recommendation is None:
                    raise FeedbackError()
                record = sanitize_feedback(
                    dict(
                        **values,
                        origin=target.origin,
                        recommendation_id=target.recommendation.recommendation_id,
                        recommendation_hash=recommendation_hash(target.recommendation),
                    )
                )
                if supersedes_id is not None:
                    parent_row = (
                        connection.execute(
                            select(RecommendationFeedback.__table__).where(
                                RecommendationFeedback.feedback_id == supersedes_id
                            )
                        )
                        .mappings()
                        .one()
                    )
                    parent = sanitize_feedback(dict(parent_row))
                    if (
                        parent.run_id != record.run_id
                        or parent.reviewer != record.reviewer
                        or parent.origin != record.origin
                        or parent.recommendation_id != record.recommendation_id
                        or parent.recommendation_hash != record.recommendation_hash
                    ):
                        raise FeedbackError()
                # Unique root/successor and composite FK also cover concurrent writes.
                connection.execute(
                    insert(RecommendationFeedback).values(**record.model_dump(mode="python"))
                )
            return record
        except Exception:
            raise FeedbackError() from None

    def list(self, *, run_id: UUID | None = None, limit: int = 25) -> tuple[FeedbackRecord, ...]:
        try:
            _limit(limit)
            if run_id is not None and type(run_id) is not UUID:
                raise FeedbackError()
            statement = select(RecommendationFeedback.__table__)
            if run_id is not None:
                statement = statement.where(RecommendationFeedback.run_id == run_id)
            statement = statement.order_by(
                RecommendationFeedback.created_at.desc(), RecommendationFeedback.feedback_id.desc()
            ).limit(limit)
            with self._engine.connect() as connection:
                rows = connection.execute(statement).mappings().all()
            return tuple(sanitize_feedback(dict(row)) for row in rows)
        except Exception:
            raise FeedbackError() from None

    def summary(self, *, origin: Origin, limit: int = 100) -> FeedbackSummary:
        try:
            _limit(limit)
            if type(origin) is not Origin:
                raise FeedbackError()
            successor = aliased(RecommendationFeedback)
            statement = (
                select(RecommendationFeedback.__table__)
                .where(
                    RecommendationFeedback.origin == origin.value,
                    ~exists().where(successor.supersedes_id == RecommendationFeedback.feedback_id),
                )
                .order_by(
                    RecommendationFeedback.created_at.desc(),
                    RecommendationFeedback.feedback_id.desc(),
                )
                .limit(limit + 1)
            )
            with self._engine.connect() as connection:
                rows = connection.execute(statement).mappings().all()
            records = tuple(sanitize_feedback(dict(row)) for row in rows)
            sample = records[:limit]
            counts = {assessment.value: 0 for assessment in Assessment}
            for record in sample:
                counts[record.assessment.value] += 1
            return FeedbackSummary(
                origin=origin,
                limit=limit,
                sample_size=len(sample),
                has_more=len(records) > limit,
                **counts,
            )
        except Exception:
            raise FeedbackError() from None

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> "PostgresFeedbackStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
