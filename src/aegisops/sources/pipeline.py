"""Owned sources for the complete pipeline; lifecycle stays within collection."""

from aegisops.domain.models import PodSnapshot
from aegisops.sources.env_guard import load_reader_identity
from aegisops.sources.kubernetes_readonly import KubernetesReadOnlySource
from aegisops.sources.safe_client import ReadOnlyClient
from aegisops.sources.synthetic import ScenarioRepository, SyntheticSource


class SyntheticBatchSource:
    def __init__(self, scenario: str | None = None) -> None:
        self._scenario = scenario

    def collect(self) -> list[PodSnapshot]:
        repository = ScenarioRepository()
        ids = (self._scenario,) if self._scenario is not None else repository.list_ids()
        return [
            snapshot for name in ids for snapshot in SyntheticSource(repository, name).collect()
        ]


class LocalKindSource:
    def collect(self) -> list[PodSnapshot]:
        reader = ReadOnlyClient(load_reader_identity())
        try:
            return KubernetesReadOnlySource(reader, include_logs=True).collect()
        finally:
            reader.close()
