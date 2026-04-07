from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class MeetingPhase(str, Enum):
    EXPLORE = "explore"
    DEBATE = "debate"
    SYNTHESIZE = "synthesize"
    DECIDE = "decide"


class ActionType(str, Enum):
    SPEAK = "speak"
    HANDOFF = "handoff"
    CHECK_CONSENSUS = "check_consensus"
    HUMAN_OVERRIDE = "human_override"
    END_MEETING = "end_meeting"
    NOOP = "noop"


@dataclass
class DecisionSignals:
    support: float = 0.0
    confidence: float = 0.0
    risk_penalty: float = 0.0
    margin_top1_top2: float = 0.0
    disagreement_index: float = 1.0

    rounds_without_progress: int = 0
    tool_failure_rate: float = 0.0

    api_unreachable: bool = False
    timeout_count: int = 0
    rate_limited_count: int = 0
    missing_required_fields_after_retry: bool = False
    human_force_complete: bool = False


@dataclass
class DecisionContext:
    room_id: str
    phase: MeetingPhase
    signals: DecisionSignals
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanResult:
    topic: str
    next_focus: str
    notes: str = ""


@dataclass
class CoordinationAction:
    action_type: ActionType
    reason: str
    target_role: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConsensusResult:
    score: float
    should_end: bool
    reason: str


@dataclass
class GuardrailDecision:
    allow: bool
    escalate: bool
    reason: str


@dataclass
class FallbackDecision:
    should_fallback: bool
    reason: str


class ModelTier(str, Enum):
    DEFAULT = "default"
    ESCALATION = "escalation"
    DISASTER_FALLBACK = "disaster_fallback"


@dataclass
class ModelTarget:
    supplier: str
    model: str


@dataclass
class RoutingDecision:
    tier: ModelTier
    target: ModelTarget
    reason: str
