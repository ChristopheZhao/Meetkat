from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class GenerateRequest:
    system_prompt: str
    user_prompt: str
    model: str
    temperature: float = 0.2


@dataclass
class GenerateResponse:
    text: str
    raw_response: str


@dataclass(frozen=True)
class ProviderConfig:
    supplier: str
    base_url: str
    api_key: str
    timeout_sec: int = 45


class LLMProvider(Protocol):
    def generate(self, req: GenerateRequest) -> GenerateResponse:
        ...
