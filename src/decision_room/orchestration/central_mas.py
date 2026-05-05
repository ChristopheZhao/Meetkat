from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from decision_room.mas.types import DecisionSignals, MeetingPhase


@dataclass(frozen=True)
class CentralAgentRole:
    role: str
    display_name: str
    mission: str
    deliverable: str
    focus_areas: list[str]
    stance: str

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AssignmentContract:
    agent: str
    run: bool
    mission: str
    deliverable: str
    constraints: list[str] = field(default_factory=list)
    order: int = 0
    runtime_hints: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentWorkProduct:
    role: CentralAgentRole
    title: str
    text: str
    claim: str
    evidence: list[str]
    confidence: float
    target_claim_ref: str = ""
    action_items: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["role"] = self.role.to_payload()
        return payload


@dataclass(frozen=True)
class SupervisorState:
    run_id: str
    round_index: int
    phase: str
    current_focus: str
    memory_projection: dict[str, Any]
    assignment_contracts: list[AssignmentContract]
    next_node: str
    gate_status: str

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["assignment_contracts"] = [
            item.to_payload() for item in self.assignment_contracts
        ]
        return payload


@dataclass(frozen=True)
class CentralizedMASRound:
    phase: MeetingPhase
    signals: DecisionSignals
    next_focus: str
    supervisor_state: SupervisorState
    role_catalog: list[CentralAgentRole]
    work_products: list[AgentWorkProduct]
    decision_candidate: str
    action_items: list[str]
    open_questions: list[str]
    agreement: list[str]
    disagreement: list[str]
    conclusion_type: str
    conclusion_reason: str
    should_end: bool


class CentralizedMeetingSupervisor:
    """Single-supervisor, shared-memory MAS for online decision rooms.

    The supervisor owns role selection and assignment contracts. Role nodes
    consume the shared room projection and return structured work products.
    Runtime and control stay outside this class.
    """

    def role_catalog(self) -> list[CentralAgentRole]:
        return [
            CentralAgentRole(
                role="implementation_specialist",
                display_name="Systems Architect",
                mission="Turn the decision into a feasible system boundary and delivery path.",
                deliverable="Architecture consequence and implementation sequence.",
                focus_areas=["architecture", "runtime boundary", "delivery path"],
                stance="feasibility",
            ),
            CentralAgentRole(
                role="product_specialist",
                display_name="Product Strategist",
                mission="Keep the decision grounded in user value and adoption friction.",
                deliverable="Product value tradeoff and scope recommendation.",
                focus_areas=["user value", "workflow fit", "scope"],
                stance="product",
            ),
            CentralAgentRole(
                role="risk_specialist",
                display_name="Risk Controller",
                mission="Expose failure modes, governance gaps, and hidden assumptions.",
                deliverable="Risk ledger with mitigations and stop conditions.",
                focus_areas=["risk", "governance", "operator control"],
                stance="risk",
            ),
            CentralAgentRole(
                role="operations_specialist",
                display_name="Decision Scribe",
                mission="Translate the discussion into decision memory and accountable actions.",
                deliverable="Decision record, open questions, and action items.",
                focus_areas=["synthesis", "traceability", "actions"],
                stance="synthesis",
            ),
        ]

    def build_round(self, snapshot: Any, round_index: int) -> CentralizedMASRound:
        phase = self._phase(snapshot, round_index)
        memory_projection = self._memory_projection(snapshot)
        selected_roles = self._select_roles(snapshot, round_index)
        assignments = self._assign(selected_roles, snapshot, round_index)
        state = SupervisorState(
            run_id=str(getattr(snapshot, "room_id", "")),
            round_index=round_index,
            phase=phase.value,
            current_focus=self._next_focus(snapshot, phase, round_index),
            memory_projection=memory_projection,
            assignment_contracts=assignments,
            next_node=assignments[0].agent if assignments else "synthesis",
            gate_status="passed",
        )
        work_products = [
            self._execute_role(role, assignment, snapshot, state, index)
            for index, (role, assignment) in enumerate(zip(selected_roles, assignments))
            if assignment.run
        ]
        decision_candidate = self._decision_candidate(snapshot, work_products, round_index)
        action_items = self._action_items(snapshot, work_products, round_index)
        open_questions = self._open_questions(snapshot, work_products, round_index)
        agreement = self._agreement(snapshot, work_products)
        disagreement = self._disagreement(snapshot, work_products)
        signals = self._signals(snapshot, round_index, work_products, open_questions)
        should_end = round_index >= 5 and signals.confidence >= 0.68
        if getattr(snapshot, "last_human_message", "") and round_index < 5:
            should_end = False
        return CentralizedMASRound(
            phase=phase,
            signals=signals,
            next_focus=state.current_focus,
            supervisor_state=state,
            role_catalog=self.role_catalog(),
            work_products=work_products,
            decision_candidate=decision_candidate,
            action_items=action_items,
            open_questions=open_questions,
            agreement=agreement,
            disagreement=disagreement,
            conclusion_type="decision_ready" if should_end else "working_consensus",
            conclusion_reason=(
                "Supervisor has enough cross-role evidence to publish a decision record."
                if should_end
                else "Supervisor is still collecting role evidence before closing the decision."
            ),
            should_end=should_end,
        )

    def _select_roles(self, snapshot: Any, round_index: int) -> list[CentralAgentRole]:
        catalog = self.role_catalog()
        corpus = self._corpus(snapshot)
        selected: list[CentralAgentRole] = []
        if any(token in corpus for token in ("architecture", "runtime", "api", "mas", "agent", "system")):
            selected.append(catalog[0])
        if any(token in corpus for token in ("user", "product", "ux", "workflow", "交互", "体验")):
            selected.append(catalog[1])
        if any(token in corpus for token in ("risk", "guardrail", "governance", "fallback", "boundary", "边界")):
            selected.append(catalog[2])
        if round_index >= 2 or any(token in corpus for token in ("decision", "deliver", "action", "交付")):
            selected.append(catalog[3])
        if len(selected) < 3:
            for role in catalog:
                if role not in selected:
                    selected.append(role)
                if len(selected) >= 3:
                    break
        if round_index >= 2 and catalog[3] not in selected:
            selected = [*selected[:2], catalog[3]]
        return selected[:4]

    def _assign(
        self,
        roles: list[CentralAgentRole],
        snapshot: Any,
        round_index: int,
    ) -> list[AssignmentContract]:
        focus = self._next_focus(snapshot, self._phase(snapshot, round_index), round_index)
        contracts: list[AssignmentContract] = []
        for index, role in enumerate(roles):
            contracts.append(
                AssignmentContract(
                    agent=role.role,
                    run=True,
                    mission=f"{role.mission} Focus this round: {focus}",
                    deliverable=role.deliverable,
                    constraints=[
                        "Use only the shared room memory projection and current requirement.",
                        "Return a concrete claim, evidence, confidence, and next action.",
                    ],
                    order=index,
                    runtime_hints={"phase": self._phase(snapshot, round_index).value},
                )
            )
        return contracts

    def _execute_role(
        self,
        role: CentralAgentRole,
        assignment: AssignmentContract,
        snapshot: Any,
        state: SupervisorState,
        index: int,
    ) -> AgentWorkProduct:
        topic = _clean(getattr(snapshot, "topic", "")) or _topic_from_requirement(
            getattr(snapshot, "requirement", "")
        )
        goal = _clean(getattr(snapshot, "goal", "")) or "produce an executable decision"
        latest_human = _clean(getattr(snapshot, "last_human_message", ""))
        prior_claim = ""
        if index > 0:
            prior_claim = f"{state.assignment_contracts[index - 1].agent}.claim"

        if role.stance == "feasibility":
            claim = "Use a single supervisor to assign specialist work while room events remain the only live communication contract."
            evidence = [
                "One supervisor keeps task routing explicit and prevents peer agents from inventing parallel owners.",
                "The room already has replayable events, so agent work products should be attached to the event journal.",
            ]
            action = "Freeze supervisor assignment contracts in every round event."
        elif role.stance == "product":
            claim = "The usable MVP is the live decision room itself, not a configuration or demo shell."
            evidence = [
                "Operators need to see why each agent is speaking and what decision artifact it produces.",
                "A deep decision problem needs visible tradeoffs, human intervention, and a clear result path.",
            ]
            action = "Make the room open with a serious default decision and visible role responsibilities."
        elif role.stance == "risk":
            claim = "The highest delivery risk is letting validation or UI convenience become a second MAS entry owner."
            evidence = [
                "Harness semantics must stay explicit and non-default.",
                "Human override, replay, and room-start contract checks are the operational stop lines.",
            ]
            action = "Keep governance checks attached to the supervisor state instead of the product surface."
        else:
            claim = "The decision should close only when the supervisor can publish rationale, risks, and accountable next steps."
            evidence = [
                "Consensus without action items is not a usable decision artifact.",
                "Open questions should stay visible instead of being buried inside transcript prose.",
            ]
            action = "Publish a compact decision record with owners, risks, and follow-up gates."

        text = (
            f"{role.display_name} assignment: {assignment.deliverable}. "
            f"For '{topic}', I recommend: {claim} "
            f"Evidence: {evidence[0]} {evidence[1]} "
            f"Next action: {action}"
        )
        if latest_human:
            text += f" Latest human input considered: {latest_human}"
        return AgentWorkProduct(
            role=role,
            title=f"{role.display_name} readout",
            text=text,
            claim=claim,
            evidence=evidence,
            confidence=round(0.68 + min(0.18, index * 0.04 + state.round_index * 0.03), 2),
            target_claim_ref=prior_claim,
            action_items=[action],
        )

    def _memory_projection(self, snapshot: Any) -> dict[str, Any]:
        return {
            "requirement": getattr(snapshot, "requirement", ""),
            "topic": getattr(snapshot, "topic", ""),
            "goal": getattr(snapshot, "goal", ""),
            "constraints": list(getattr(snapshot, "constraints", []) or [])[:6],
            "open_questions": list(getattr(snapshot, "open_questions", []) or [])[:4],
            "transcript_depth": len(getattr(snapshot, "transcript", []) or []),
            "last_human_message": getattr(snapshot, "last_human_message", ""),
            "candidate_decision": getattr(snapshot, "candidate_decision", ""),
        }

    def _phase(self, snapshot: Any, round_index: int) -> MeetingPhase:
        if round_index <= 1:
            return MeetingPhase.EXPLORE
        if getattr(snapshot, "last_human_message", ""):
            return MeetingPhase.DEBATE
        if getattr(snapshot, "candidate_decision", ""):
            return MeetingPhase.DECIDE
        return MeetingPhase.SYNTHESIZE

    def _next_focus(self, snapshot: Any, phase: MeetingPhase, round_index: int) -> str:
        if getattr(snapshot, "last_human_message", ""):
            return "reconcile the latest human intervention with the supervisor decision record"
        if round_index <= 1:
            return "assign specialist agents and expose the first decision tradeoffs"
        if phase == MeetingPhase.SYNTHESIZE:
            return "merge architecture, product, and risk evidence into one executable recommendation"
        return "lock the final decision record and follow-up gates"

    def _decision_candidate(
        self,
        snapshot: Any,
        work_products: list[AgentWorkProduct],
        round_index: int,
    ) -> str:
        topic = _clean(getattr(snapshot, "topic", "")) or _topic_from_requirement(
            getattr(snapshot, "requirement", "")
        )
        claims = [item.claim for item in work_products[:3]]
        return (
            f"Adopt a centralized supervisor MAS for '{topic}' with shared room memory, "
            "explicit assignment contracts, event-backed communication, and a product UI "
            f"that exposes role evidence before convergence. Core basis: {' '.join(claims)}"
        )

    def _action_items(
        self,
        snapshot: Any,
        work_products: list[AgentWorkProduct],
        round_index: int,
    ) -> list[str]:
        del snapshot
        items: list[str] = []
        for product in work_products:
            for item in product.action_items:
                if item not in items:
                    items.append(item)
        if round_index >= 2:
            items.append("Run browser-level acceptance on room creation, live stream, human message, and results handoff.")
        return items[:6]

    def _open_questions(
        self,
        snapshot: Any,
        work_products: list[AgentWorkProduct],
        round_index: int,
    ) -> list[str]:
        questions = list(getattr(snapshot, "open_questions", []) or [])
        if round_index <= 1:
            questions.append("Which governance gate should block release if a future feature bypasses supervisor assignment contracts?")
        if any(product.role.stance == "risk" for product in work_products):
            questions.append("What evidence is sufficient to prove the online communication path is real rather than mocked?")
        deduped: list[str] = []
        for item in questions:
            normalized = _clean(item)
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        return deduped[:4] if round_index < 2 else deduped[:2]

    def _agreement(self, snapshot: Any, work_products: list[AgentWorkProduct]) -> list[str]:
        del snapshot
        agreements = [
            "A single supervisor should own task routing and assignment contracts.",
            "Shared room memory and replayable events should remain the communication substrate.",
        ]
        if any(product.role.stance == "product" for product in work_products):
            agreements.append("The first screen must be the working room workflow, not a marketing shell.")
        return agreements

    def _disagreement(self, snapshot: Any, work_products: list[AgentWorkProduct]) -> list[str]:
        del snapshot
        if any(product.role.stance == "risk" for product in work_products):
            return [
                "How strict the release gate should be before allowing future harness-specific changes."
            ]
        return ["How much detail the supervisor should expose in the live UI by default."]

    def _signals(
        self,
        snapshot: Any,
        round_index: int,
        work_products: list[AgentWorkProduct],
        open_questions: list[str],
    ) -> DecisionSignals:
        transcript_depth = len(getattr(snapshot, "transcript", []) or [])
        support = min(0.94, 0.58 + round_index * 0.09 + len(work_products) * 0.025)
        confidence = min(0.90, 0.60 + round_index * 0.08 + len(work_products) * 0.02)
        disagreement = max(0.16, 0.58 - round_index * 0.16 - transcript_depth * 0.01)
        if open_questions:
            confidence = max(0.48, confidence - min(0.08, len(open_questions) * 0.02))
            disagreement = min(0.72, disagreement + min(0.08, len(open_questions) * 0.015))
        return DecisionSignals(
            support=support,
            confidence=confidence,
            risk_penalty=min(0.28, 0.08 + len(open_questions) * 0.025),
            margin_top1_top2=min(0.24, 0.06 + round_index * 0.05),
            disagreement_index=disagreement,
            rounds_without_progress=0 if round_index <= 2 else 1,
            tool_failure_rate=0.0,
        )

    def _corpus(self, snapshot: Any) -> str:
        values = [
            getattr(snapshot, "requirement", ""),
            getattr(snapshot, "topic", ""),
            getattr(snapshot, "goal", ""),
            getattr(snapshot, "current_focus", ""),
            *list(getattr(snapshot, "constraints", []) or []),
            *list(getattr(snapshot, "open_questions", []) or []),
        ]
        return " ".join(_clean(item).lower() for item in values)


def _topic_from_requirement(requirement: str) -> str:
    text = _clean(requirement)
    if not text:
        return "Deep decision room"
    sentence = re.split(r"[.!?。！？]", text, maxsplit=1)[0].strip()
    return sentence[:86].rstrip() + ("..." if len(sentence) > 86 else "")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
