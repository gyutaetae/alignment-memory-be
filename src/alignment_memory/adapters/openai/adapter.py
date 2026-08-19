from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alignment_memory.adapters.openrouter.adapter import OpenRouterAdapter, OpenRouterConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenAIConfig(OpenRouterConfig):
    base_url: str = "https://api.openai.com/v1"


class OpenAIAdapter(OpenRouterAdapter):
    """OpenAI Chat Completions adapter with strict structured output validation."""

    provider_name = "openai"
    provider_label = "OpenAI"

    def _provider_request_fields(self) -> dict[str, Any]:
        return {}
