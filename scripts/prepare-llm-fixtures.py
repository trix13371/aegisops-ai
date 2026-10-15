"""Handcrafted offline replies keyed by canonical prompt hash; never call a provider."""

import json
from pathlib import Path

from aegisops.actions.catalog import load_catalog
from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.llm.base import AnalysisDraft
from aegisops.llm.prompt import build_request
from aegisops.sources.synthetic import ScenarioRepository

root = Path(__file__).resolve().parents[1]
catalog = load_catalog()
repository = ScenarioRepository()
records = {}
for scenario_id in repository.list_ids():
    scenario, snapshot = repository.load(scenario_id)
    incidents = DetectionEngine().detect([snapshot])
    for incident in incidents:
        context = ContextBuilder().build(incident)
        request = build_request(incident, context, catalog)
        records[request.prompt_hash] = AnalysisDraft.model_validate(
            {
                "diagnosis": {
                    "summary": "Учебная фикстура: вероятная причина требует проверки оператором",
                    "hypotheses": [
                        {
                            "cause_category": scenario.cause_category.value,
                            "cause": "Учебный пример " + scenario.cause_category.value,
                            "rationale": "Учебная фикстура для проверки механики конвейера",
                            "confidence": "low",
                            "supporting_evidence_ids": [context.evidence[0].evidence_id],
                        }
                    ],
                    "missing_evidence": ["Сверить гипотезу с оператором"],
                },
                "recommendation": {
                    "title": "Проверить инцидент вручную",
                    "rationale": "Учебная рекомендация из каталога без выполнения действий",
                    "proposed_action": {"action_id": "escalate_to_human", "params": {}},
                    "explanation_steps": ["Просмотреть очищенные evidence"],
                },
            }
        ).model_dump(mode="json")

(root / "fixtures/llm/analysis-v2.json").write_text(
    json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(f"{len(records)} handcrafted replies generated; no provider calls.")
