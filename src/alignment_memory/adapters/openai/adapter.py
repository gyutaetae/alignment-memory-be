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

    def _response_schema(self) -> dict[str, Any]:
        schema = super()._response_schema()
        self._remove_unsupported_formats(schema)
        return schema

    @classmethod
    def _remove_unsupported_formats(cls, value: object) -> None:
        if isinstance(value, dict):
            value.pop("format", None)
            for child in value.values():
                cls._remove_unsupported_formats(child)
        elif isinstance(value, list):
            for child in value:
                cls._remove_unsupported_formats(child)
