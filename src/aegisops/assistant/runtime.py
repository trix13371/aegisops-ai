"""Process-environment runtime selection; the only module that reads env vars."""

import os

from aegisops.assistant.models import LEGACY, ModelName, Selection
from aegisops.projects.models import ProjectError

PROVIDER_VAR = "AEGISOPS_ASSISTANT_PROVIDER"
MODEL_VAR = "AEGISOPS_ASSISTANT_MODEL"


def _read(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    if not raw or raw != raw.strip():
        raise ProjectError("Некорректное значение переменной окружения ассистента")
    return raw


def runtime_selection() -> Selection:
    """Resolve the runtime selection from the environment."""
    provider = _read(PROVIDER_VAR)
    model = _read(MODEL_VAR)
    if provider is None and model is None:
        return LEGACY
    if provider is None or model is None:
        raise ProjectError("Задайте и провайдера, и модель ассистента")
    try:
        return Selection.model_validate({"provider": provider, "model": model})
    except Exception:
        raise ProjectError("Провайдер или модель ассистента не поддерживаются") from None


def runtime_model() -> ModelName:
    """Return the approved model name for the runtime selection."""
    return runtime_selection().model
