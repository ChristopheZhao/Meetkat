from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Participant:
    participant_id: str
    role: str
    identity: str
    display_name: str
    activation: str = ""
    speaking: bool = True
    capability_profile: str = ""
    join_reason: str = ""
    focus_areas: list[str] = field(default_factory=list)


@dataclass
class TranscriptEntry:
    message_id: str
    seq: int
    ts_ms: int
    role: str
    producer_id: str
    event_type: str
    text: str
    title: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConsensusState:
    score: float = 0.0
    should_end: bool = False
    reason: str = ""
    support: float = 0.0
    confidence: float = 0.0
    disagreement_index: float = 1.0
    margin_top1_top2: float = 0.0


@dataclass
class RoomSnapshot:
    room_id: str
    requirement: str = ""
    topic: str = ""
    goal: str = ""
    brief_source: str = "fallback"
    brief_source_reason: str = ""
    mode: str = "agent_first"
    status: str = "starting"
    phase: str = "explore"
    round_index: int = 0
    current_focus: str = ""
    current_turns: list[dict[str, str]] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    planning_artifacts: dict[str, Any] = field(default_factory=dict)
    participants: list[Participant] = field(default_factory=list)
    transcript: list[TranscriptEntry] = field(default_factory=list)
    live_chunks: dict[str, str] = field(default_factory=dict)
    consensus: ConsensusState = field(default_factory=ConsensusState)
    candidate_decision: str = ""
    action_items: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    last_human_message: str = ""
    last_override: str = ""
    conclusion_type: str = ""
    conclusion_reason: str = ""
    # Optional next-phase recommendation from the most recent synthesis LLM
    # turn. ``_phase_for_round`` prefers this over the rule-based derivation
    # when present and valid; rules become the fallback path.
    recommended_next_phase: str = ""
    ended_reason: str = ""
    control_reason: str = ""
    orchestration_end_reason: str = ""
    resume_token: str = ""
    created_at_ms: int = 0
    updated_at_ms: int = 0
    last_seq: int = 0

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)

    def room_summary(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "requirement": self.requirement,
            "topic": self.topic,
            "goal": self.goal,
            "brief_source": self.brief_source,
            "brief_source_reason": self.brief_source_reason,
            "mode": self.mode,
            "status": self.status,
            "phase": self.phase,
            "round_index": self.round_index,
            "current_focus": self.current_focus,
            "last_seq": self.last_seq,
            "updated_at_ms": self.updated_at_ms,
            "created_at_ms": self.created_at_ms,
            "candidate_decision": self.candidate_decision,
            "conclusion_type": self.conclusion_type,
        }
