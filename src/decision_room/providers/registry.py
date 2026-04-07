from __future__ import annotations

from typing import Dict

from .base import LLMProvider, ProviderConfig
from .openai_compatible import OpenAICompatibleProvider


class ProviderRegistry:
    """Maps supplier ids to provider instances.

    For now every supplier is assumed to expose an OpenAI-compatible
    `chat/completions` endpoint. The registry keeps the runtime/provider
    separation so supplier switching stays configuration-driven.
    """

    def __init__(self, providers: Dict[str, LLMProvider]) -> None:
        self._providers = providers

    @classmethod
    def from_openai_compatible_configs(
        cls, configs: Dict[str, ProviderConfig]
    ) -> "ProviderRegistry":
        providers: Dict[str, LLMProvider] = {}
        for supplier, cfg in configs.items():
            providers[supplier] = OpenAICompatibleProvider(
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                timeout_sec=cfg.timeout_sec,
            )
        return cls(providers)

    def get(self, supplier: str) -> LLMProvider:
        try:
            return self._providers[supplier]
        except KeyError as exc:
            raise KeyError(f"unknown supplier: {supplier}") from exc
