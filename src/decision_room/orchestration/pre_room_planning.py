from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol

from .brief_planner import (
    MeetingBrief,
    RequirementPlanningService,
    RoomStartContractDraft,
)


@dataclass(frozen=True)
class CandidateSpecialist:
    role: str
    display_name: str
    capability_profile: str
    prompt_contract: str
    join_reason: str
    focus_areas: list[str] = field(default_factory=list)
    ttl_rounds: int = 2
    turn_budget: int = 1

    def to_payload(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: object) -> "CandidateSpecialist | None":
        if not isinstance(payload, dict):
            return None
        role = str(payload.get("role", "")).strip().lower()
        display_name = str(payload.get("display_name", "")).strip()
        capability_profile = str(payload.get("capability_profile", "")).strip()
        prompt_contract = str(payload.get("prompt_contract", "")).strip()
        join_reason = str(payload.get("join_reason", "")).strip()
        if not all([role, display_name, capability_profile, prompt_contract, join_reason]):
            return None
        focus_areas = payload.get("focus_areas", [])
        return cls(
            role=role,
            display_name=display_name,
            capability_profile=capability_profile,
            prompt_contract=prompt_contract,
            join_reason=join_reason,
            focus_areas=[
                str(item).strip() for item in focus_areas if str(item).strip()
            ]
            if isinstance(focus_areas, list)
            else [],
            ttl_rounds=max(1, int(payload.get("ttl_rounds", 2))),
            turn_budget=max(1, int(payload.get("turn_budget", 1))),
        )


@dataclass(frozen=True)
class AgentProfile:
    participant_id: str
    role: str
    identity: str
    display_name: str
    avatar: str
    activation: str
    speaking: bool
    capability_profile: str
    prompt_contract: str
    join_reason: str
    ttl_rounds: int
    turn_budget: int
    focus_areas: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: object) -> "AgentProfile | None":
        if not isinstance(payload, dict):
            return None
        participant_id = str(payload.get("participant_id", "")).strip()
        role = str(payload.get("role", "")).strip().lower()
        identity = str(payload.get("identity", "")).strip()
        display_name = str(payload.get("display_name", "")).strip()
        avatar = str(payload.get("avatar", "")).strip()
        activation = str(payload.get("activation", "")).strip()
        capability_profile = str(payload.get("capability_profile", "")).strip()
        prompt_contract = str(payload.get("prompt_contract", "")).strip()
        join_reason = str(payload.get("join_reason", "")).strip()
        if not all(
            [
                participant_id,
                role,
                identity,
                display_name,
                avatar,
                activation,
                capability_profile,
                prompt_contract,
                join_reason,
            ]
        ):
            return None
        focus_areas = payload.get("focus_areas", [])
        return cls(
            participant_id=participant_id,
            role=role,
            identity=identity,
            display_name=display_name,
            avatar=avatar,
            activation=activation,
            speaking=bool(payload.get("speaking", False)),
            capability_profile=capability_profile,
            prompt_contract=prompt_contract,
            join_reason=join_reason,
            ttl_rounds=max(0, int(payload.get("ttl_rounds", 0))),
            turn_budget=max(0, int(payload.get("turn_budget", 0))),
            focus_areas=[
                str(item).strip() for item in focus_areas if str(item).strip()
            ]
            if isinstance(focus_areas, list)
            else [],
        )


@dataclass(frozen=True)
class PreRoomPlan:
    requirement: str
    topic: str
    meeting_objective: str
    initial_focus: str
    constraints: list[str]
    open_questions: list[str]
    room_start_contract_draft: RoomStartContractDraft
    brief_source: str
    brief_source_reason: str
    candidate_specialist_roster: list[CandidateSpecialist]
    agent_profiles: list[AgentProfile]

    @property
    def active_agents(self) -> list[AgentProfile]:
        return [
            profile
            for profile in self.agent_profiles
            if profile.activation == "persistent" and profile.speaking
        ]

    def planning_payload(self) -> dict[str, object]:
        return {
            "requirement": self.requirement,
            "topic": self.topic,
            "meeting_objective": self.meeting_objective,
            "initial_focus": self.initial_focus,
            "constraints": list(self.constraints),
            "brief_source": self.brief_source,
            "brief_source_reason": self.brief_source_reason,
            "candidate_specialist_roster": [
                item.to_payload() for item in self.candidate_specialist_roster
            ],
            "agent_profiles": [item.to_payload() for item in self.agent_profiles],
        }


class RolePlanner(Protocol):
    def plan_roles(self, brief: MeetingBrief) -> list[CandidateSpecialist]:
        ...


class RoleValidator(Protocol):
    def validate_roles(
        self,
        brief: MeetingBrief,
        candidates: list[CandidateSpecialist],
    ) -> list[CandidateSpecialist]:
        ...


class AgentFactory(Protocol):
    def build_profiles(
        self,
        brief: MeetingBrief,
        specialists: list[CandidateSpecialist],
    ) -> list[AgentProfile]:
        ...


class PreRoomPlanningWorkflow:
    def __init__(
        self,
        requirement_planner: RequirementPlanningService,
        role_planner: RolePlanner | None = None,
        role_validator: RoleValidator | None = None,
        agent_factory: AgentFactory | None = None,
    ) -> None:
        self._requirement_planner = requirement_planner
        # When no role_planner is injected we fall back to the keyword-based
        # HeuristicRolePlanner. That is a rule-based code path — track the
        # selection so the runtime can surface "degraded role planning" to
        # the operator UI instead of silently shipping rule-based selection.
        if role_planner is None:
            self._role_planner = HeuristicRolePlanner()
            self._role_planner_kind = "heuristic"
        else:
            self._role_planner = role_planner
            self._role_planner_kind = "llm"
        self._role_validator = role_validator or DefaultRoleValidator()
        self._agent_factory = agent_factory or DefaultAgentFactory()

    @property
    def role_planner_kind(self) -> str:
        return self._role_planner_kind

    @classmethod
    def from_env(cls) -> "PreRoomPlanningWorkflow":
        return cls(requirement_planner=RequirementPlanningService.from_env())

    def plan_room(self, requirement: str, *, allow_fallback: bool = False) -> PreRoomPlan:
        brief = self._requirement_planner.plan_requirement(
            requirement,
            allow_fallback=allow_fallback,
        )
        candidate_roles = self._role_planner.plan_roles(brief)
        validated_roles = self._role_validator.validate_roles(brief, candidate_roles)
        agent_profiles = self._agent_factory.build_profiles(brief, validated_roles)
        return PreRoomPlan(
            requirement=brief.requirement,
            topic=brief.topic,
            meeting_objective=brief.goal,
            initial_focus=brief.current_focus,
            constraints=list(brief.constraints),
            open_questions=list(brief.open_questions),
            room_start_contract_draft=brief.room_start_contract,
            brief_source=brief.brief_source,
            brief_source_reason=brief.brief_source_reason,
            candidate_specialist_roster=validated_roles,
            agent_profiles=agent_profiles,
        )


def planned_specialists_from_snapshot(snapshot: object) -> list[CandidateSpecialist]:
    planning_artifacts = getattr(snapshot, "planning_artifacts", {})
    if isinstance(planning_artifacts, dict):
        raw = planning_artifacts.get("candidate_specialist_roster", [])
        if isinstance(raw, list):
            parsed = [
                candidate
                for candidate in (
                    CandidateSpecialist.from_payload(item) for item in raw
                )
                if candidate is not None
            ]
            if parsed:
                return parsed
    return _fallback_specialists()


def planned_agent_profiles_from_snapshot(snapshot: object) -> list[AgentProfile]:
    planning_artifacts = getattr(snapshot, "planning_artifacts", {})
    if isinstance(planning_artifacts, dict):
        raw = planning_artifacts.get("agent_profiles", [])
        if isinstance(raw, list):
            parsed = [
                profile
                for profile in (AgentProfile.from_payload(item) for item in raw)
                if profile is not None
            ]
            if parsed:
                return parsed
    return []


def planned_agent_profile_for_role(snapshot: object, role: str) -> AgentProfile | None:
    normalized_role = role.strip().lower()
    for profile in planned_agent_profiles_from_snapshot(snapshot):
        if profile.role == normalized_role:
            return profile
    return None


def resolve_turn_specialists(
    snapshot: object,
    requested_roles: list[str],
) -> list[CandidateSpecialist]:
    candidates = planned_specialists_from_snapshot(snapshot)
    by_role = {candidate.role: candidate for candidate in candidates}
    selected: list[CandidateSpecialist] = []
    for role in requested_roles:
        normalized_role = role.strip().lower()
        candidate = by_role.get(normalized_role)
        if candidate is None or any(item.role == candidate.role for item in selected):
            continue
        selected.append(candidate)
    if selected:
        return selected

    fallback = _fallback_specialists()
    if not candidates:
        return fallback[:2]
    return candidates[: min(len(candidates), 2)]


_ROLE_BLUEPRINTS = (
    {
        "role": "implementation_specialist",
        "display_name": "Implementation Specialist",
        "keywords": (
            "engineering",
            "architecture",
            "api",
            "backend",
            "runtime",
            "system",
            "integration",
            "performance",
            "scal",
            "latency",
        ),
        "capability_profile": "Evaluates technical feasibility, integration shape, and implementation complexity.",
        "prompt_contract": "Answer with concrete implementation constraints, sequence risks, and delivery tradeoffs.",
        "join_reason": "The brief contains material technical execution work that needs targeted engineering judgment.",
        "focus_areas": ["feasibility", "integration", "delivery sequencing"],
    },
    {
        "role": "risk_specialist",
        "display_name": "Risk Specialist",
        "keywords": (
            "risk",
            "failure",
            "fallback",
            "guardrail",
            "timeout",
            "override",
            "reliab",
            "safety",
            "rollback",
            "resume",
        ),
        "capability_profile": "Surfaces failure modes, brittleness, and control-plane edge cases before the room converges.",
        "prompt_contract": "Challenge assumptions and keep unresolved execution, safety, and recovery risks explicit.",
        "join_reason": "The brief carries delivery or control-path risk that cannot be left implicit.",
        "focus_areas": ["failure analysis", "recovery", "operational risk"],
    },
    {
        "role": "product_specialist",
        "display_name": "Product Specialist",
        "keywords": (
            "product",
            "customer",
            "user",
            "workflow",
            "experience",
            "value",
            "requirement",
            "decision",
            "ux",
            "frontend",
            "ui",
        ),
        "capability_profile": "Keeps the room aligned with user goals, product tradeoffs, and visible workflow outcomes.",
        "prompt_contract": "Argue from requirement fit, user impact, and product-level tradeoffs without inventing new scope.",
        "join_reason": "The brief includes user-facing workflow choices that need product grounding.",
        "focus_areas": ["requirement fit", "user impact", "scope control"],
    },
    {
        "role": "operations_specialist",
        "display_name": "Operations Specialist",
        "keywords": (
            "ops",
            "monitor",
            "demo",
            "deploy",
            "incident",
            "replay",
            "transport",
            "event",
            "runtime",
            "debug",
        ),
        "capability_profile": "Focuses on runtime observability, operator workflows, and day-two execution concerns.",
        "prompt_contract": "Answer with concrete operational implications, observability needs, and runtime supportability.",
        "join_reason": "The brief references runtime visibility, replay, or operator handling.",
        "focus_areas": ["observability", "operator workflow", "supportability"],
    },
)


class HeuristicRolePlanner:
    def plan_roles(self, brief: MeetingBrief) -> list[CandidateSpecialist]:
        corpus = " ".join(
            [
                brief.requirement,
                brief.topic,
                brief.goal,
                brief.current_focus,
                *brief.constraints,
                *brief.open_questions,
            ]
        ).lower()
        scored: list[tuple[int, int, dict[str, object]]] = []
        for index, blueprint in enumerate(_ROLE_BLUEPRINTS):
            score = sum(1 for keyword in blueprint["keywords"] if keyword in corpus)
            if score > 0:
                scored.append((score, -index, blueprint))

        scored.sort(reverse=True)
        selected = [item[2] for item in scored[:3]]
        if not selected:
            selected = [
                _ROLE_BLUEPRINTS[0],
                _ROLE_BLUEPRINTS[1],
            ]
        elif len(selected) == 1:
            fallback = (
                _ROLE_BLUEPRINTS[1]
                if selected[0]["role"] != "risk_specialist"
                else _ROLE_BLUEPRINTS[0]
            )
            selected.append(fallback)

        if self._matches_blueprint(corpus, _ROLE_BLUEPRINTS[0]):
            selected = self._ensure_role(selected, _ROLE_BLUEPRINTS[0])
        if self._matches_blueprint(corpus, _ROLE_BLUEPRINTS[1]):
            selected = self._ensure_role(selected, _ROLE_BLUEPRINTS[1])

        return [
            CandidateSpecialist(
                role=str(item["role"]),
                display_name=str(item["display_name"]),
                capability_profile=str(item["capability_profile"]),
                prompt_contract=str(item["prompt_contract"]),
                join_reason=str(item["join_reason"]),
                focus_areas=list(item["focus_areas"]),
            )
            for item in selected
        ]

    def _ensure_role(
        self,
        selected: list[dict[str, object]],
        blueprint: dict[str, object],
    ) -> list[dict[str, object]]:
        if any(item["role"] == blueprint["role"] for item in selected):
            return selected
        trimmed = list(selected[:2]) if len(selected) >= 3 else list(selected)
        trimmed.append(blueprint)
        return trimmed

    def _matches_blueprint(self, corpus: str, blueprint: dict[str, object]) -> bool:
        return any(keyword in corpus for keyword in blueprint["keywords"])


class DefaultRoleValidator:
    _RESERVED_ROLES = {
        "host",
        "recorder",
        "meeting_planner",
        "role_planner",
        "role_validator",
        "agent_factory",
    }

    def validate_roles(
        self,
        brief: MeetingBrief,
        candidates: list[CandidateSpecialist],
    ) -> list[CandidateSpecialist]:
        del brief
        seen: set[str] = set()
        validated: list[CandidateSpecialist] = []
        for candidate in candidates:
            role = candidate.role.strip().lower()
            if not role or role in self._RESERVED_ROLES or role in seen:
                continue
            seen.add(role)
            validated.append(candidate)

        if not validated:
            raise ValueError("pre-room role planning produced no usable specialist candidates")
        return validated


class DefaultAgentFactory:
    def build_profiles(
        self,
        brief: MeetingBrief,
        specialists: list[CandidateSpecialist],
    ) -> list[AgentProfile]:
        profiles = [
            AgentProfile(
                participant_id="agent.host.1",
                role="host",
                identity="agent",
                display_name="Host",
                avatar="moderator",
                activation="persistent",
                speaking=True,
                capability_profile=(
                    "Moderates the room, reads memory projections, decides what information "
                    "is missing, and chooses whether to invite specialists or conclude."
                ),
                prompt_contract=(
                    "Own the next-step decision, decide who should speak, and keep the room "
                    "aligned to the current meeting objective."
                ),
                join_reason="Host is the only persistent active agent in the P1 meeting topology.",
                ttl_rounds=0,
                turn_budget=0,
                focus_areas=["moderation", "handoff", "conclusion gating"],
            )
        ]

        for candidate in specialists:
            profiles.append(
                AgentProfile(
                    participant_id=f"agent.{candidate.role}.1",
                    role=candidate.role,
                    identity="agent",
                    display_name=candidate.display_name,
                    avatar="specialist",
                    activation="on_demand",
                    speaking=True,
                    capability_profile=candidate.capability_profile,
                    prompt_contract=candidate.prompt_contract,
                    join_reason=candidate.join_reason,
                    ttl_rounds=candidate.ttl_rounds,
                    turn_budget=candidate.turn_budget,
                    focus_areas=list(candidate.focus_areas),
                )
            )

        profiles.append(
            AgentProfile(
                participant_id="capability.synthesis.1",
                role="synthesis",
                identity="capability",
                display_name="Synthesis",
                avatar="notebook",
                activation="memory_backed",
                speaking=False,
                capability_profile=(
                    "Aggregates room memory into candidate decisions, action items, open "
                    "questions, and agreement/disagreement summaries."
                ),
                prompt_contract=(
                    "Produce structured meeting synthesis from memory projections without "
                    "becoming a permanent speaking role."
                ),
                join_reason=(
                    "P1 keeps structured synthesis as a first-class output while avoiding a "
                    "permanent speaking synthesis agent."
                ),
                ttl_rounds=0,
                turn_budget=0,
                focus_areas=[
                    "candidate decision",
                    "action items",
                    "agreement summary",
                    "disagreement summary",
                ],
            )
        )
        return profiles


def _fallback_specialists() -> list[CandidateSpecialist]:
    return [
        CandidateSpecialist(
            role=str(_ROLE_BLUEPRINTS[0]["role"]),
            display_name=str(_ROLE_BLUEPRINTS[0]["display_name"]),
            capability_profile=str(_ROLE_BLUEPRINTS[0]["capability_profile"]),
            prompt_contract=str(_ROLE_BLUEPRINTS[0]["prompt_contract"]),
            join_reason=str(_ROLE_BLUEPRINTS[0]["join_reason"]),
            focus_areas=list(_ROLE_BLUEPRINTS[0]["focus_areas"]),
        ),
        CandidateSpecialist(
            role=str(_ROLE_BLUEPRINTS[1]["role"]),
            display_name=str(_ROLE_BLUEPRINTS[1]["display_name"]),
            capability_profile=str(_ROLE_BLUEPRINTS[1]["capability_profile"]),
            prompt_contract=str(_ROLE_BLUEPRINTS[1]["prompt_contract"]),
            join_reason=str(_ROLE_BLUEPRINTS[1]["join_reason"]),
            focus_areas=list(_ROLE_BLUEPRINTS[1]["focus_areas"]),
        ),
    ]
