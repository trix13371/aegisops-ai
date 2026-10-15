"""Common minimal authorization inputs; no credentials or live clients."""

import pytest

from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.domain.authorization import AuthorizationControl
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.sources.synthetic import ScenarioRepository


@pytest.fixture
def authz_case():
    incident = DetectionEngine().detect([ScenarioRepository().load("crashloop-oom")[1]])[0]
    result = AnalysisEngine(FakeLLMClient()).analyze(incident, ContextBuilder().build(incident))
    return incident, result.recommendation


@pytest.fixture
def open_control():
    return AuthorizationControl(generation=1, global_blocked=False)
