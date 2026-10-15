"""Source adapter for the shared processing kernel; reader lifecycle stays here."""

from aegisops.domain.models import Origin
from aegisops.pipeline.models import PipelineResult
from aegisops.pipeline.processing import BatchProcessor, PipelineError
from aegisops.sources.base import ObservationSource
from aegisops.sources.env_guard import EnvironmentGuardError
from aegisops.sources.kubernetes_readonly import CollectionError
from aegisops.sources.safe_client import ReadOnlyError

__all__ = ["Pipeline", "PipelineError"]


class Pipeline(BatchProcessor):
    def run(self, source: ObservationSource, *, origin: Origin) -> PipelineResult:
        if type(origin) is not Origin:
            raise PipelineError()
        try:
            raw = source.collect()
        except (EnvironmentGuardError, ReadOnlyError, CollectionError):
            return self._scan_record(origin, 0, "environment_rejected")
        except Exception:
            return self._scan_record(origin, 0, "collection_failed")
        return self.process(raw, origin=origin)
