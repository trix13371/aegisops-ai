"""Validation helpers for AegisOps F02 human feedback.

Pure functions only: no IO, no network, no logging, no printing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from aegisops.context.sanitizer import sanitize_text
from aegisops.domain.models import Recommendation
from aegisops.feedback.models import FeedbackError, FeedbackRecord

__all__ = [
    "recommendation_hash",
    "sanitize_feedback",
]

_MAX_SERIALIZED_BYTES = 16384


def recommendation_hash(recommendation: Recommendation) -> str:
    """Return the canonical SHA-256 hex digest of a recommendation.

    The recommendation is revalidated and dumped in JSON mode with sorted
    keys, no ASCII escaping, compact separators, and NaN disallowed.
    """
    try:
        revalidated = Recommendation.model_validate(recommendation)
        payload = revalidated.model_dump(mode="json")
        canonical = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except Exception:
        raise FeedbackError() from None


def sanitize_feedback(record: object) -> FeedbackRecord:
    """Validate and sanitize a feedback record.

    The record is first validated as a :class:`FeedbackRecord`. Then
    ``comment``, ``reviewer``, and ``recommendation_id`` are passed through
    :func:`sanitize_text`. ``reviewer`` and ``recommendation_id`` must not
    change (identity must never silently mutate); ``comment`` is replaced
    with its sanitized form. The updated mapping is revalidated and the
    serialized JSON must be at most 16384 UTF-8 bytes.
    """
    try:
        validated = FeedbackRecord.model_validate(record)

        sanitized_comment = sanitize_text(validated.comment)
        sanitized_reviewer = sanitize_text(validated.reviewer)
        sanitized_recommendation_id = sanitize_text(validated.recommendation_id)

        if sanitized_reviewer != validated.reviewer:
            raise FeedbackError()
        if sanitized_recommendation_id != validated.recommendation_id:
            raise FeedbackError()

        updated: dict[str, Any] = validated.model_dump(mode="python")
        updated["comment"] = sanitized_comment

        result = FeedbackRecord.model_validate(updated)

        serialized = json.dumps(
            result.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(serialized.encode("utf-8")) > _MAX_SERIALIZED_BYTES:
            raise FeedbackError()

        return result
    except FeedbackError:
        raise
    except Exception:
        raise FeedbackError() from None
