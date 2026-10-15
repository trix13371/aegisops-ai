"""Bounded developer/CI readiness check through the decision API only."""

import time

from aegisops.authorization.bundle import load_config
from aegisops.authorization.http import OpaClient
from aegisops.authorization.projection import build_input
from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.policy.checker import PolicyChecker
from aegisops.sources.synthetic import ScenarioRepository

incident = DetectionEngine().detect([ScenarioRepository().load("crashloop-oom")[1]])[0]
result = AnalysisEngine(FakeLLMClient()).analyze(incident, ContextBuilder().build(incident))
request = build_input(
    incident,
    result.recommendation,
    PolicyChecker().evaluate(incident, result.recommendation),
    load_config(),
)
for attempt in range(10):
    try:
        if not OpaClient().evaluate(request).allow:
            raise ValueError("unexpected denial")
        break
    except Exception:
        if attempt == 9:
            raise SystemExit("OPA decision API not ready or mismatched.") from None
        time.sleep(0.2)
print("OPA fixed decision API ready; no execution authority.")
