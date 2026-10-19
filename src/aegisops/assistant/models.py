"""Closed provider/model catalog and immutable selection DTO.

No arbitrary endpoints or model names are accepted. No secrets are stored here.
"""

from typing import Literal

from aegisops.domain.common import DTO, ShortText

ProviderName = Literal["openrouter", "deepseek", "gemini", "openai"]
ModelName = Literal[
    "deepseek/deepseek-v4.1-flash",
    "deepseek-flash",
    "gemini-3.8-flash",
    "gpt-5.4-mini",
]

LEGACY_PROVIDER: ProviderName = "openrouter"
LEGACY_MODEL: ModelName = "deepseek/deepseek-v4.1-flash"


class CatalogEntry(DTO):
    """One approved provider/model pair. Contains no credentials."""

    label: ShortText
    host: ShortText
    inference_path: ShortText
    protocol: Literal["openai-chat", "gemini-generate"]
    model: ModelName


CATALOG: dict[ProviderName, CatalogEntry] = {
    "openrouter": CatalogEntry(
        label="OpenRouter (legacy)",
        host="openrouter.ai",
        inference_path="/api/v1/chat/completions",
        protocol="openai-chat",
        model="deepseek/deepseek-v4.1-flash",
    ),
    "deepseek": CatalogEntry(
        label="DeepSeek",
        host="api.deepseek.com",
        inference_path="/chat/completions",
        protocol="openai-chat",
        model="deepseek-flash",
    ),
    "gemini": CatalogEntry(
        label="Google Gemini",
        host="generativelanguage.googleapis.com",
        inference_path="/v1beta/models/{model}:generateContent",
        protocol="gemini-generate",
        model="gemini-3.8-flash",
    ),
    "openai": CatalogEntry(
        label="OpenAI",
        host="api.openai.com",
        inference_path="/v1/chat/completions",
        protocol="openai-chat",
        model="gpt-5.4-mini",
    ),
}


class Selection(DTO):
    """Serializable provider/model choice. Defaults to the legacy pair."""

    provider: ProviderName = LEGACY_PROVIDER
    model: ModelName = LEGACY_MODEL

    def model_post_init(self, __context: object) -> None:
        entry = CATALOG.get(self.provider)
        if entry is None or entry.model != self.model:
            raise ValueError("provider/model pair is not in the approved catalog")


def selected(provider: ProviderName) -> Selection:
    """Pure helper: build the approved Selection for a provider."""
    entry = CATALOG.get(provider)
    if entry is None:
        raise ValueError("provider is not in the approved catalog")
    return Selection(provider=provider, model=entry.model)


LEGACY = Selection()
