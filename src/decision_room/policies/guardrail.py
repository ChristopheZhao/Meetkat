from __future__ import annotations

from dataclasses import dataclass

from decision_room.mas.types import DecisionContext, GuardrailDecision


@dataclass
class GuardrailConfig:
    max_rounds_without_progress: int = 3
    max_tool_failure_rate: float = 0.30


class BasicGuardrailPolicy:
    """Guardrails are gates only; they do not decide meeting content."""

    def __init__(self, cfg: GuardrailConfig | None = None) -> None:
        self.cfg = cfg or GuardrailConfig()

    def check(self, ctx: DecisionContext) -> GuardrailDecision:
        s = ctx.signals

        if s.rounds_without_progress > self.cfg.max_rounds_without_progress:
            return GuardrailDecision(
                allow=False,
                escalate=True,
                reason="stalled rounds exceed guardrail",
            )

        if s.tool_failure_rate > self.cfg.max_tool_failure_rate:
            return GuardrailDecision(
                allow=False,
                escalate=True,
                reason="tool failure rate exceeds guardrail",
            )

        return GuardrailDecision(allow=True, escalate=False, reason="within guardrail")
