from __future__ import annotations

from typing import Protocol

from .types import (
    ConsensusResult,
    CoordinationAction,
    DecisionContext,
    FallbackDecision,
    GuardrailDecision,
    PlanResult,
)


class PlanningStrategy(Protocol):
    def plan(self, ctx: DecisionContext) -> PlanResult:
        ...


class CoordinationStrategy(Protocol):
    def next_action(self, ctx: DecisionContext) -> CoordinationAction:
        ...


class ConsensusStrategy(Protocol):
    def evaluate(self, ctx: DecisionContext) -> ConsensusResult:
        ...


class GuardrailPolicy(Protocol):
    def check(self, ctx: DecisionContext) -> GuardrailDecision:
        ...


class FallbackPolicy(Protocol):
    def check(self, ctx: DecisionContext) -> FallbackDecision:
        ...
