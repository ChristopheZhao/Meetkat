from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from decision_room.mas.hybrid import (
    HybridConsensusStrategy,
    HybridCoordinationStrategy,
    HybridPlanningStrategy,
)
from decision_room.mas.types import (
    ActionType,
    CoordinationAction,
    DecisionContext,
    DecisionSignals,
    MeetingPhase,
    ModelTarget,
    RoutingDecision,
)
from decision_room.policies.fallback import DisasterOnlyFallbackPolicy
from decision_room.policies.routing_control import RoutingControlPolicy
from decision_room.providers import (
    GenerateRequest,
    ProviderConfig,
    ProviderHTTPError,
    ProviderNetworkError,
    ProviderRegistry,
    ProviderTimeoutError,
)
from decision_room.routing.model_router import HybridModelRouter, RouterTargets

from .pre_room_planning import (
    CandidateSpecialist,
    planned_specialists_from_snapshot,
    resolve_turn_specialists,
)
from .preflight import question_answered_by_context
from .real_run_contract import (
    HostAgenda,
    build_host_prompts,
    extract_json_object,
    parse_host_agenda,
)


@dataclass(frozen=True)
class RoomMessage:
    role: str
    title: str
    text: str
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoomRound:
    phase: MeetingPhase
    signals: DecisionSignals
    plan_topic: str
    next_focus: str
    coordination: CoordinationAction
    consensus_score: float
    consensus_should_end: bool
    should_end: bool
    consensus_reason: str
    end_reason: str
    messages: list[RoomMessage]
    decision_candidate: str
    action_items: list[str]
    open_questions: list[str]
    summary_text: str
    conclusion_type: str
    conclusion_reason: str
    synthesis_message: RoomMessage


class RoomExecutor(Protocol):
    async def build_round(self, snapshot: Any, round_index: int) -> RoomRound:
        ...


class UnavailableRoomExecutor:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    @property
    def reason(self) -> str:
        return self._reason

    async def build_round(self, snapshot: Any, round_index: int) -> RoomRound:
        raise RuntimeError(f"room executor unavailable: {self._reason}")


@dataclass(frozen=True)
class ArgumentOutput:
    title: str
    text: str
    claim: str
    evidence: list[str]
    confidence: float
    target_claim_ref: str


@dataclass(frozen=True)
class SynthesisOutput:
    title: str
    text: str
    agreement: list[str]
    disagreement: list[str]
    open_questions: list[str]
    decision_candidate: str
    action_item_draft: list[str]
    conclusion_type: str
    conclusion_reason: str
    should_end_meeting: bool


@dataclass(frozen=True)
class SpecialistTurnResult:
    specialist: CandidateSpecialist
    task: str
    output: ArgumentOutput
    route: RoutingDecision


@dataclass(frozen=True)
class RouteExecution:
    text: str
    route: RoutingDecision
    ctx: DecisionContext


class LLMRoomExecutor:
    def __init__(
        self,
        registry: ProviderRegistry,
        router: HybridModelRouter,
        planner: HybridPlanningStrategy | None = None,
        coordination: HybridCoordinationStrategy | None = None,
        consensus: HybridConsensusStrategy | None = None,
        use_background_threads: bool = True,
        transient_max_attempts: int = 2,
    ) -> None:
        self._registry = registry
        self._router = router
        self._planner = planner or HybridPlanningStrategy()
        self._coordination = coordination or HybridCoordinationStrategy()
        self._consensus = consensus or HybridConsensusStrategy()
        self._use_background_threads = use_background_threads
        self._transient_max_attempts = max(1, transient_max_attempts)

    @classmethod
    def from_env(cls) -> RoomExecutor:
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> RoomExecutor:
        try:
            default_supplier = _env("MODEL_DEFAULT_SUPPLIER", env)
            default_model = _env("MODEL_DEFAULT_MODEL", env)
            escalation_supplier = _env("MODEL_ESCALATION_SUPPLIER", env)
            escalation_model = _env("MODEL_ESCALATION_MODEL", env)
            fallback_supplier = _env("MODEL_FALLBACK_SUPPLIER", env)
            fallback_model = _env("MODEL_FALLBACK_MODEL", env)

            supplier_ids = {default_supplier, escalation_supplier, fallback_supplier}
            registry = ProviderRegistry.from_openai_compatible_configs(
                {supplier: _provider_config(supplier, env) for supplier in supplier_ids}
            )
            router = HybridModelRouter(
                RoutingControlPolicy(DisasterOnlyFallbackPolicy()),
                targets=RouterTargets(
                    default_target=_target(default_supplier, default_model),
                    escalation_target=_target(escalation_supplier, escalation_model),
                    disaster_fallback_target=_target(fallback_supplier, fallback_model),
                ),
            )
        except Exception as exc:
            return UnavailableRoomExecutor(str(exc))
        return cls(
            registry=registry,
            router=router,
            transient_max_attempts=_env_int_optional(
                "MODEL_REQUEST_MAX_ATTEMPTS", 2, env
            ),
        )

    async def build_round(self, snapshot: Any, round_index: int) -> RoomRound:
        phase = self._phase_for_round(snapshot, round_index)
        routing_signals = self._signals_for_round(snapshot, round_index)
        topic = snapshot.topic or "multi-agent meeting room"
        ctx = DecisionContext(
            room_id=snapshot.room_id,
            phase=phase,
            signals=routing_signals,
            metadata={"topic": topic},
        )

        plan = self._planner.plan(ctx)
        next_focus = self._resolve_focus(snapshot, plan.next_focus)
        route_ctx = ctx
        route = self._router.route(route_ctx)
        brief = _build_runtime_meeting_brief(snapshot, next_focus)
        allowed_constraint_ids = {item["id"] for item in brief["constraints"]}
        allowed_specialist_roles = {
            item["role"] for item in brief.get("candidate_specialists", []) if item.get("role")
        }
        host_system_prompt, host_user_prompt = build_host_prompts(brief)
        host_execution = await self._generate_text(
            route_ctx=route_ctx,
            route=route,
            system_prompt=host_system_prompt,
            user_prompt=host_user_prompt,
            role="host",
        )
        route_ctx = host_execution.ctx
        host_route = host_execution.route
        route = host_route
        host_agenda = parse_host_agenda(
            host_execution.text,
            allowed_constraint_ids,
            allowed_specialist_roles,
        )
        host_agenda = _filter_host_agenda_open_questions(snapshot, host_agenda)
        specialist_turns = [
            (specialist, turn.task)
            for specialist, turn in zip(
                resolve_turn_specialists(
                    snapshot,
                    [turn.role for turn in host_agenda.turns],
                ),
                host_agenda.turns,
            )
            if specialist.role == turn.role
        ]
        if not specialist_turns:
            raise ValueError("host agenda did not resolve to any valid specialist turns")

        coordination = self._coordination_for_turns(ctx, specialist_turns)

        host_message = self._build_host_message(
            snapshot=snapshot,
            phase=phase,
            round_index=round_index,
            next_focus=next_focus,
            route=route,
            host_agenda=host_agenda,
            target_roles=[specialist.role for specialist, _task in specialist_turns],
        )
        turn_results: list[SpecialistTurnResult] = []
        target_claim_ref = ""
        for specialist, turn_task in specialist_turns:
            output, route_ctx, turn_route = await self._generate_argument(
                route_ctx=route_ctx,
                route=route,
                specialist=specialist,
                turn_task=turn_task,
                snapshot=snapshot,
                phase=phase,
                round_index=round_index,
                next_focus=next_focus,
                host_agenda=host_agenda,
                target_claim_ref=target_claim_ref,
            )
            route = turn_route
            turn_results.append(
                SpecialistTurnResult(
                    specialist=specialist,
                    task=turn_task,
                    output=output,
                    route=turn_route,
                )
            )
            target_claim_ref = output.claim

        synthesis_output, route_ctx, synthesis_route = await self._generate_synthesis(
            route_ctx=route_ctx,
            route=route,
            snapshot=snapshot,
            phase=phase,
            round_index=round_index,
            next_focus=next_focus,
            host_agenda=host_agenda,
            turn_results=turn_results,
        )

        open_questions = _dedupe_strings(
            [
                *_visible_open_questions(snapshot),
                *host_agenda.open_questions,
                *_filter_answered_questions(snapshot, synthesis_output.open_questions),
            ],
            limit=6,
        )
        post_signals = self._signals_after_round(
            snapshot=snapshot,
            round_index=round_index,
            open_questions=open_questions,
            synthesis_output=synthesis_output,
        )
        consensus = self._consensus.evaluate(
            DecisionContext(
                room_id=snapshot.room_id,
                phase=phase,
                signals=post_signals,
                metadata={"topic": topic},
            )
        )

        messages = [host_message]
        for turn_index, turn_result in enumerate(turn_results, start=1):
            messages.append(
                RoomMessage(
                    role=turn_result.specialist.role,
                    title=turn_result.output.title,
                    text=turn_result.output.text,
                    artifacts={
                        "claim": turn_result.output.claim,
                        "evidence": turn_result.output.evidence,
                        "confidence": turn_result.output.confidence,
                        "target_claim_ref": turn_result.output.target_claim_ref,
                        "specialist_display_name": turn_result.specialist.display_name,
                        "capability_profile": turn_result.specialist.capability_profile,
                        "turn_task": turn_result.task,
                        "turn_index": turn_index,
                        "route": _route_artifact(turn_result.route),
                    },
                )
            )
        synthesis_message = RoomMessage(
            role="synthesis",
            title=synthesis_output.title,
            text=synthesis_output.text,
            artifacts={
                "agreement": synthesis_output.agreement,
                "disagreement": synthesis_output.disagreement,
                "open_questions": open_questions,
                "decision_candidate": synthesis_output.decision_candidate,
                "action_item_draft": synthesis_output.action_item_draft,
                "conclusion_type": synthesis_output.conclusion_type,
                "conclusion_reason": synthesis_output.conclusion_reason,
                "route": _route_artifact(synthesis_route),
            },
        )
        summary_text = (
            f"{synthesis_output.text} Conclusion: {synthesis_output.conclusion_type}. "
            f"{synthesis_output.conclusion_reason} Consensus score {consensus.score:.2f}. "
            f"{consensus.reason}."
        )
        should_end, end_reason = self._resolve_round_end_signal(
            snapshot=snapshot,
            round_index=round_index,
            synthesis_output=synthesis_output,
            consensus=consensus,
        )

        return RoomRound(
            phase=phase,
            signals=post_signals,
            plan_topic=plan.topic,
            next_focus=next_focus,
            coordination=coordination,
            consensus_score=consensus.score,
            consensus_should_end=consensus.should_end,
            should_end=should_end,
            consensus_reason=consensus.reason,
            end_reason=end_reason,
            messages=messages,
            decision_candidate=synthesis_output.decision_candidate,
            action_items=synthesis_output.action_item_draft,
            open_questions=open_questions,
            summary_text=summary_text,
            conclusion_type=synthesis_output.conclusion_type,
            conclusion_reason=synthesis_output.conclusion_reason,
            synthesis_message=synthesis_message,
        )

    async def _generate_argument(
        self,
        *,
        route_ctx: DecisionContext,
        route: RoutingDecision,
        specialist: CandidateSpecialist,
        turn_task: str,
        snapshot: Any,
        phase: MeetingPhase,
        round_index: int,
        next_focus: str,
        host_agenda: Any,
        target_claim_ref: str,
    ) -> tuple[ArgumentOutput, DecisionContext, RoutingDecision]:
        system_prompt, user_prompt = _build_argument_prompts(
            specialist=specialist,
            turn_task=turn_task,
            snapshot=snapshot,
            phase=phase,
            round_index=round_index,
            next_focus=next_focus,
            host_agenda=host_agenda,
            target_claim_ref=target_claim_ref,
            route=route,
        )
        execution = await self._generate_text(
            route_ctx=route_ctx,
            route=route,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            role=specialist.role,
        )
        return (
            _parse_argument_output(
                execution.text,
                role=specialist.role,
            ),
            execution.ctx,
            execution.route,
        )

    async def _generate_synthesis(
        self,
        *,
        route_ctx: DecisionContext,
        route: RoutingDecision,
        snapshot: Any,
        phase: MeetingPhase,
        round_index: int,
        next_focus: str,
        host_agenda: Any,
        turn_results: list[SpecialistTurnResult],
    ) -> tuple[SynthesisOutput, DecisionContext, RoutingDecision]:
        system_prompt, user_prompt = _build_synthesis_prompts(
            snapshot=snapshot,
            phase=phase,
            round_index=round_index,
            next_focus=next_focus,
            host_agenda=host_agenda,
            turn_results=turn_results,
            route=route,
        )
        execution = await self._generate_text(
            route_ctx=route_ctx,
            route=route,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            role="synthesis",
        )
        return _parse_synthesis_output(execution.text), execution.ctx, execution.route

    async def _generate_text(
        self,
        *,
        route_ctx: DecisionContext,
        route: RoutingDecision,
        system_prompt: str,
        user_prompt: str,
        role: str,
    ) -> RouteExecution:
        request = GenerateRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=route.target.model,
            temperature=0.2,
        )
        try:
            text = await self._generate_with_same_route_retries(route, request)
            return RouteExecution(text=text, route=route, ctx=route_ctx)
        except (ProviderTimeoutError, ProviderNetworkError, ProviderHTTPError) as exc:
            fallback_ctx = self._ctx_after_provider_failure(route_ctx, exc)
            fallback_route = self._router.route(fallback_ctx)
            if fallback_route.target == route.target:
                raise RuntimeError(
                    "room executor model request failed: "
                    f"role={role}; "
                    f"supplier={route.target.supplier}; "
                    f"model={route.target.model}; "
                    f"attempts={self._transient_max_attempts}; "
                    f"reason={exc}"
                ) from exc

            fallback_request = GenerateRequest(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=fallback_route.target.model,
                temperature=0.2,
            )
            try:
                text = await self._generate_with_same_route_retries(
                    fallback_route,
                    fallback_request,
                )
                return RouteExecution(text=text, route=fallback_route, ctx=fallback_ctx)
            except Exception as fallback_exc:
                raise RuntimeError(
                    "room executor model request failed after control reroute: "
                    f"role={role}; "
                    f"primary={route.target.supplier}/{route.target.model}; "
                    f"rerouted={fallback_route.target.supplier}/{fallback_route.target.model}; "
                    f"primary_reason={exc}; "
                    f"reroute_reason={fallback_exc}"
                ) from fallback_exc
        except Exception as exc:
            raise RuntimeError(
                "room executor model request failed: "
                f"role={role}; "
                f"supplier={route.target.supplier}; "
                f"model={route.target.model}; "
                f"reason={exc}"
            ) from exc

    async def _generate_with_same_route_retries(
        self,
        route: RoutingDecision,
        request: GenerateRequest,
    ) -> str:
        provider = self._registry.get(route.target.supplier)
        last_exc: Exception | None = None
        for attempt in range(1, self._transient_max_attempts + 1):
            try:
                if self._use_background_threads:
                    response = await asyncio.to_thread(provider.generate, request)
                else:
                    response = provider.generate(request)
                return response.text
            except (ProviderTimeoutError, ProviderNetworkError, ProviderHTTPError) as exc:
                last_exc = exc
                if attempt >= self._transient_max_attempts:
                    break
                await asyncio.sleep(0.35 * attempt)

        assert last_exc is not None
        raise last_exc

    def _ctx_after_provider_failure(
        self,
        ctx: DecisionContext,
        exc: Exception,
    ) -> DecisionContext:
        signals = ctx.signals
        next_signals = DecisionSignals(
            support=signals.support,
            confidence=signals.confidence,
            risk_penalty=signals.risk_penalty,
            margin_top1_top2=signals.margin_top1_top2,
            disagreement_index=signals.disagreement_index,
            rounds_without_progress=signals.rounds_without_progress,
            tool_failure_rate=signals.tool_failure_rate,
            api_unreachable=signals.api_unreachable,
            timeout_count=signals.timeout_count,
            rate_limited_count=signals.rate_limited_count,
            missing_required_fields_after_retry=signals.missing_required_fields_after_retry,
            human_force_complete=signals.human_force_complete,
        )
        if isinstance(exc, ProviderTimeoutError):
            next_signals.timeout_count = max(
                next_signals.timeout_count,
                self._transient_max_attempts,
            )
        elif isinstance(exc, ProviderNetworkError):
            next_signals.api_unreachable = True
        elif isinstance(exc, ProviderHTTPError):
            if exc.status_code == 429:
                next_signals.rate_limited_count = max(
                    next_signals.rate_limited_count,
                    self._transient_max_attempts,
                )
            elif exc.status_code is not None and exc.status_code >= 500:
                next_signals.api_unreachable = True

        return DecisionContext(
            room_id=ctx.room_id,
            phase=ctx.phase,
            signals=next_signals,
            metadata=dict(ctx.metadata),
        )

    def _build_host_message(
        self,
        *,
        snapshot: Any,
        phase: MeetingPhase,
        round_index: int,
        next_focus: str,
        route: RoutingDecision,
        host_agenda: Any,
        target_roles: list[str],
    ) -> RoomMessage:
        focus_lines = [
            f"{idx}. {point.title}: {point.reason}"
            for idx, point in enumerate(host_agenda.focus_points, start=1)
        ]
        text = (
            f"Round {round_index} enters {phase.value}. "
            f"Meeting focus: {next_focus}\n"
            + "\n".join(focus_lines)
        )
        if snapshot.last_human_message:
            text += f"\nHuman input to incorporate: {snapshot.last_human_message}"
        if host_agenda.open_questions:
            text += "\nOpen questions: " + "; ".join(host_agenda.open_questions)
        return RoomMessage(
            role="host",
            title="Round focus",
            text=text,
            artifacts={
                "next_focus": next_focus,
                "round_goal": snapshot.goal,
                "target_roles": target_roles,
                "focus_points": [
                    {
                        "title": point.title,
                        "reason": point.reason,
                        "constraint_ids": point.constraint_ids,
                    }
                    for point in host_agenda.focus_points
                ],
                "turns": [
                    {
                        "role": turn.role,
                        "task": turn.task,
                    }
                    for turn in host_agenda.turns
                ],
                "open_questions": host_agenda.open_questions,
                "route": _route_artifact(route),
            },
        )

    def _coordination_for_turns(
        self,
        ctx: DecisionContext,
        specialist_turns: list[tuple[CandidateSpecialist, str]],
    ) -> CoordinationAction:
        coordination = self._coordination.next_action(ctx)
        if coordination.action_type not in {ActionType.HANDOFF, ActionType.SPEAK}:
            return coordination
        if not specialist_turns:
            return CoordinationAction(
                action_type=coordination.action_type,
                reason=coordination.reason,
            )
        target_role = specialist_turns[0][0].role
        return CoordinationAction(
            action_type=coordination.action_type,
            reason=coordination.reason,
            target_role=target_role,
            payload=dict(coordination.payload),
        )

    def _resolve_focus(self, snapshot: Any, default_focus: str) -> str:
        if snapshot.last_human_message:
            return (
                "address the latest human intervention while preserving replay, "
                "human override, and room-level event visibility"
            )
        return snapshot.current_focus or default_focus

    def _phase_for_round(self, snapshot: Any, round_index: int) -> MeetingPhase:
        if round_index <= 1 and not snapshot.transcript:
            return MeetingPhase.EXPLORE
        if snapshot.candidate_decision:
            if _visible_open_questions(snapshot) or snapshot.consensus.disagreement_index > 0.35:
                return MeetingPhase.SYNTHESIZE
            return MeetingPhase.DECIDE
        if snapshot.last_human_message:
            return MeetingPhase.DEBATE
        if len(snapshot.transcript) >= 4 or snapshot.consensus.support >= 0.70:
            return MeetingPhase.SYNTHESIZE
        return MeetingPhase.DEBATE

    def _signals_for_round(self, snapshot: Any, round_index: int) -> DecisionSignals:
        prior_consensus = getattr(snapshot, "consensus", None)
        transcript_depth = len(getattr(snapshot, "transcript", []))
        open_question_count = len(_visible_open_questions(snapshot))
        support = (
            prior_consensus.support
            if prior_consensus is not None and prior_consensus.support > 0
            else 0.56
        )
        confidence = (
            prior_consensus.confidence
            if prior_consensus is not None and prior_consensus.confidence > 0
            else 0.63
        )
        disagreement = (
            prior_consensus.disagreement_index
            if prior_consensus is not None and transcript_depth > 0
            else 0.58
        )
        margin = (
            prior_consensus.margin_top1_top2
            if prior_consensus is not None and prior_consensus.margin_top1_top2 > 0
            else 0.05
        )

        support = min(0.94, support + 0.03 + min(0.06, transcript_depth * 0.01))
        confidence = min(0.94, confidence + 0.02 + min(0.05, transcript_depth * 0.008))
        disagreement = max(0.18, disagreement - min(0.10, transcript_depth * 0.015))
        margin = min(0.24, margin + 0.02 + min(0.05, transcript_depth * 0.008))

        if snapshot.candidate_decision:
            support = min(0.96, support + 0.05)
            confidence = min(0.96, confidence + 0.04)
            disagreement = max(0.15, disagreement - 0.05)
            margin = min(0.28, margin + 0.03)
        if open_question_count:
            confidence = max(0.42, confidence - min(0.10, open_question_count * 0.02))
            disagreement = min(0.90, disagreement + min(0.10, open_question_count * 0.015))
        if snapshot.last_human_message:
            support = min(0.96, support + 0.02)
            confidence = min(0.96, confidence + 0.03)
            disagreement = max(0.16, disagreement - 0.03)

        return DecisionSignals(
            support=support,
            confidence=confidence,
            risk_penalty=min(0.32, 0.08 + open_question_count * 0.03),
            margin_top1_top2=margin,
            disagreement_index=disagreement,
            rounds_without_progress=1 if snapshot.candidate_decision and round_index > 1 else 0,
            tool_failure_rate=0.02,
        )

    def _signals_after_round(
        self,
        *,
        snapshot: Any,
        round_index: int,
        open_questions: list[str],
        synthesis_output: SynthesisOutput,
    ) -> DecisionSignals:
        signals = self._signals_for_round(snapshot, round_index)
        open_question_penalty = min(0.12, len(open_questions) * 0.03)
        disagreement_adjustment = min(0.16, len(synthesis_output.disagreement) * 0.04)
        agreement_boost = min(0.12, len(synthesis_output.agreement) * 0.03)
        action_item_boost = 0.04 if synthesis_output.action_item_draft else 0.0

        candidate_stable = bool(
            snapshot.candidate_decision
            and snapshot.candidate_decision.strip() == synthesis_output.decision_candidate.strip()
        )
        rounds_without_progress = 1 if candidate_stable and round_index > 1 else 0

        return DecisionSignals(
            support=min(0.97, signals.support + agreement_boost + action_item_boost),
            confidence=max(0.30, min(0.97, signals.confidence - open_question_penalty + 0.02)),
            risk_penalty=min(0.35, signals.risk_penalty + open_question_penalty / 2),
            margin_top1_top2=min(0.24, signals.margin_top1_top2 + agreement_boost / 2),
            disagreement_index=max(
                0.15,
                min(0.95, signals.disagreement_index + disagreement_adjustment - agreement_boost),
            ),
            rounds_without_progress=rounds_without_progress,
            tool_failure_rate=signals.tool_failure_rate,
        )

    def _resolve_round_end_signal(
        self,
        *,
        snapshot: Any,
        round_index: int,
        synthesis_output: SynthesisOutput,
        consensus: Any,
    ) -> tuple[bool, str]:
        if consensus.should_end:
            return True, consensus.reason
        if synthesis_output.should_end_meeting:
            return True, synthesis_output.conclusion_reason
        if self._is_stable_follow_up_outcome(
            snapshot=snapshot,
            round_index=round_index,
            synthesis_output=synthesis_output,
        ):
            return (
                True,
                "orchestration detected a stable follow-up outcome across consecutive rounds",
            )
        return False, ""

    def _is_stable_follow_up_outcome(
        self,
        *,
        snapshot: Any,
        round_index: int,
        synthesis_output: SynthesisOutput,
    ) -> bool:
        if round_index < 2:
            return False
        if synthesis_output.conclusion_type != "follow_up_required":
            return False
        prior_conclusion_type = str(getattr(snapshot, "conclusion_type", "")).strip().lower()
        if prior_conclusion_type != "follow_up_required":
            return False
        prior_candidate = _normalize_text(getattr(snapshot, "candidate_decision", ""))
        current_candidate = _normalize_text(synthesis_output.decision_candidate)
        if not prior_candidate or not current_candidate or prior_candidate != current_candidate:
            return False
        return bool(synthesis_output.action_item_draft)


def _build_runtime_meeting_brief(snapshot: Any, next_focus: str) -> dict[str, Any]:
    constraints = [
        {"id": f"C{idx}", "text": item}
        for idx, item in enumerate(snapshot.constraints, start=1)
    ]
    if not constraints:
        constraints = [{"id": "C1", "text": "No explicit constraint was captured for this room."}]
    return {
        "requirement": snapshot.requirement,
        "topic": snapshot.topic,
        "goal": snapshot.goal,
        "next_focus": next_focus,
        "objective": snapshot.goal,
        "constraints": constraints,
        "open_questions": _visible_open_questions(snapshot),
        "validated_context": _validated_context_for_prompt(snapshot),
        "candidate_specialists": [
            _specialist_for_prompt(item) for item in planned_specialists_from_snapshot(snapshot)
        ],
    }


def _build_argument_prompts(
    *,
    specialist: CandidateSpecialist,
    turn_task: str,
    snapshot: Any,
    phase: MeetingPhase,
    round_index: int,
    next_focus: str,
    host_agenda: Any,
    target_claim_ref: str,
    route: RoutingDecision,
) -> tuple[str, str]:
    schema = {
        "title": "short title",
        "text": "1-3 concise paragraphs",
        "claim": "main position",
        "evidence": ["supporting point"],
        "confidence": 0.72,
        "target_claim_ref": "claim being supported or challenged",
    }
    turn_mode = "respond to a visible prior claim" if target_claim_ref else "introduce a grounded specialist contribution"
    system_prompt = (
        f"You are the {specialist.display_name} in a multi-agent decision room. "
        f"Your capability profile is: {specialist.capability_profile} "
        f"Your job in this turn is to {turn_mode}. "
        "Stay grounded in the room brief, recent agenda, and visible blackboard state. "
        "Treat room_state.validated_context as already resolved input rather than an open question surface. "
        "Do not invent constraints or hidden requirements. "
        "Clarification protocol: if your turn task has a material ambiguity that "
        "would meaningfully change your claim (a missing definition, an unstated "
        "scope boundary, a needed assumption you have no basis for), do not "
        "fabricate. Ask the operator one focused clarifying question in your "
        "`text` field, set `claim` to start with '[Awaiting operator clarification]' "
        "followed by the same one-line question, set `confidence` to 0.30 or "
        "lower, and provide your best partial reasoning in `evidence`. The "
        "operator answers via the room human-message channel and the answer "
        "appears as `room_state.last_human_message` next round so you can "
        "complete the analysis. Use this protocol sparingly — only when guessing "
        "would distort the decision record. "
        "Return exactly one JSON object and nothing else."
    )
    user_prompt = (
        "Room state:\n"
        f"{json.dumps(_room_state_for_prompt(snapshot, phase, round_index, next_focus), ensure_ascii=False, indent=2)}\n\n"
        "Host agenda:\n"
        f"{json.dumps(_host_agenda_for_prompt(host_agenda), ensure_ascii=False, indent=2)}\n\n"
        "Routing info:\n"
        f"{json.dumps(_route_artifact(route), ensure_ascii=False, indent=2)}\n\n"
        "Specialist profile:\n"
        f"{json.dumps(_specialist_for_prompt(specialist), ensure_ascii=False, indent=2)}\n\n"
        "Task:\n"
        f"- Execute this host-assigned turn task: {turn_task}\n"
        f"- Keep the analysis within the {specialist.role} specialist remit.\n"
        "- Keep the message concise and operator-readable.\n"
        "- Extract one clear claim.\n"
        "- Provide 2-4 factual evidence bullets.\n"
        "- Emit confidence as a float between 0 and 1.\n"
        f"- target_claim_ref should be {json.dumps(target_claim_ref)} if provided, otherwise use an empty string.\n"
        "- If target_claim_ref is present, respond directly to that visible claim instead of starting a disconnected thread.\n\n"
        "Output schema example:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )
    return system_prompt, user_prompt


def _build_synthesis_prompts(
    *,
    snapshot: Any,
    phase: MeetingPhase,
    round_index: int,
    next_focus: str,
    host_agenda: Any,
    turn_results: list[SpecialistTurnResult],
    route: RoutingDecision,
) -> tuple[str, str]:
    schema = {
        "title": "short title",
        "text": "synthesis-readable summary",
        "agreement": ["agreed point"],
        "disagreement": [],
        "open_questions": ["question still unresolved"],
        "decision_candidate": "candidate decision",
        "action_item_draft": ["next action"],
        "conclusion_type": "follow_up_required",
        "conclusion_reason": "why this round currently lands on that conclusion type",
        "should_end_meeting": False,
    }
    system_prompt = (
        "You are the synthesis capability in a multi-agent decision room. "
        "Aggregate the visible host and specialist outputs into a stable meeting state. "
        "Treat room_state.validated_context as authoritative pre-room context. "
        "Do not restate validated_context facts as open_questions or blocker reasons. "
        "Do not invent hidden arguments or new hard constraints. "
        "Return exactly one JSON object and nothing else."
    )
    user_prompt = (
        "Room state:\n"
        f"{json.dumps(_room_state_for_prompt(snapshot, phase, round_index, next_focus), ensure_ascii=False, indent=2)}\n\n"
        "Host agenda:\n"
        f"{json.dumps(_host_agenda_for_prompt(host_agenda), ensure_ascii=False, indent=2)}\n\n"
        "Specialist turns:\n"
        f"{json.dumps([_turn_result_for_prompt(item) for item in turn_results], ensure_ascii=False, indent=2)}\n\n"
        "Routing info:\n"
        f"{json.dumps(_route_artifact(route), ensure_ascii=False, indent=2)}\n\n"
        "Task:\n"
        "- Summarize the current agreement and disagreement.\n"
        "- Use empty lists when there is no agreement or no active disagreement yet.\n"
        "- Treat room_state.validated_context as already resolved input.\n"
        "- Preserve unresolved items as open_questions instead of hiding them.\n"
        "- Produce one candidate decision that is actionable for the current MVP phase.\n"
        "- Produce 2-4 executable action items.\n\n"
        "- Pick exactly one conclusion_type from: candidate_ready, follow_up_required, blocked, human_decision_needed.\n"
        "- conclusion_reason must explain the current semantic outcome of the meeting, not the control budget.\n\n"
        "- Do not use facts already covered by room_state.validated_context as the reason for follow-up or blocked status.\n"
        "- Set should_end_meeting to true only when the meeting should stop now and hand off to execution, follow-up, or human decision outside the room.\n"
        "- Set should_end_meeting to false when another in-room round is still necessary to resolve the current discussion.\n\n"
        "Output schema example:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )
    return system_prompt, user_prompt


def _parse_argument_output(raw: str, *, role: str) -> ArgumentOutput:
    payload = _load_json_payload(raw, role)
    title = _require_non_empty_str(payload.get("title"), f"{role}.title")
    text = _require_non_empty_str(payload.get("text"), f"{role}.text")
    claim = _require_non_empty_str(payload.get("claim"), f"{role}.claim")
    evidence = _require_string_list(payload.get("evidence"), f"{role}.evidence", min_items=1)
    confidence = _require_float(payload.get("confidence"), f"{role}.confidence")
    target_claim_ref = _optional_string(payload.get("target_claim_ref"))
    return ArgumentOutput(
        title=title,
        text=text,
        claim=claim,
        evidence=evidence,
        confidence=confidence,
        target_claim_ref=target_claim_ref,
    )


def _parse_synthesis_output(raw: str) -> SynthesisOutput:
    payload = _load_json_payload(raw, "synthesis")
    title = _require_non_empty_str(payload.get("title"), "synthesis.title")
    text = _require_non_empty_str(payload.get("text"), "synthesis.text")
    agreement = _require_string_list(
        payload.get("agreement", []),
        "synthesis.agreement",
        min_items=0,
        max_items=6,
    )
    disagreement = _require_string_list(
        payload.get("disagreement", []),
        "synthesis.disagreement",
        min_items=0,
        max_items=6,
    )
    open_questions = _require_string_list(
        payload.get("open_questions", []),
        "synthesis.open_questions",
        min_items=0,
        max_items=6,
    )
    if not agreement and not disagreement and not open_questions:
        raise ValueError(
            "synthesis output must contain at least one agreement, disagreement, or open question"
        )
    decision_candidate = _require_non_empty_str(
        payload.get("decision_candidate"),
        "synthesis.decision_candidate",
    )
    action_item_draft = _require_string_list(
        payload.get("action_item_draft"),
        "synthesis.action_item_draft",
        min_items=1,
        max_items=4,
    )
    conclusion_type = _require_conclusion_type(payload.get("conclusion_type"))
    conclusion_reason = _require_non_empty_str(
        payload.get("conclusion_reason"),
        "synthesis.conclusion_reason",
    )
    should_end_meeting = _coerce_should_end_meeting(
        payload.get("should_end_meeting"),
        conclusion_type=conclusion_type,
        disagreement=disagreement,
        open_questions=open_questions,
    )
    return SynthesisOutput(
        title=title,
        text=text,
        agreement=agreement,
        disagreement=disagreement,
        open_questions=open_questions,
        decision_candidate=decision_candidate,
        action_item_draft=action_item_draft,
        conclusion_type=conclusion_type,
        conclusion_reason=conclusion_reason,
        should_end_meeting=should_end_meeting,
    )


def _load_json_payload(raw: str, role: str) -> dict[str, Any]:
    try:
        payload = json.loads(extract_json_object(raw))
    except Exception as exc:
        raise ValueError(f"{role} response must contain one valid JSON object: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{role} response must be a JSON object")
    return payload


def _room_state_for_prompt(
    snapshot: Any,
    phase: MeetingPhase,
    round_index: int,
    next_focus: str,
) -> dict[str, Any]:
    transcript_slice = [
        {
            "role": entry.role,
            "title": entry.title,
            "text": _truncate(entry.text, 200),
        }
        for entry in snapshot.transcript[-4:]
    ]
    return {
        "requirement": snapshot.requirement,
        "topic": snapshot.topic,
        "goal": snapshot.goal,
        "phase": phase.value,
        "round_index": round_index,
        "current_focus": next_focus,
        "constraints": snapshot.constraints,
        "open_questions": _visible_open_questions(snapshot),
        "validated_context": _validated_context_for_prompt(snapshot),
        "candidate_decision": snapshot.candidate_decision,
        "action_items": snapshot.action_items,
        "last_human_message": snapshot.last_human_message,
        "available_specialists": [
            _specialist_for_prompt(item) for item in planned_specialists_from_snapshot(snapshot)
        ],
        "recent_transcript": transcript_slice,
    }


def _specialist_for_prompt(specialist: CandidateSpecialist) -> dict[str, Any]:
    return {
        "role": specialist.role,
        "display_name": specialist.display_name,
        "capability_profile": specialist.capability_profile,
        "prompt_contract": specialist.prompt_contract,
        "join_reason": specialist.join_reason,
        "focus_areas": specialist.focus_areas,
        "ttl_rounds": specialist.ttl_rounds,
        "turn_budget": specialist.turn_budget,
    }


def _host_agenda_for_prompt(host_agenda: Any) -> dict[str, Any]:
    return {
        "focus_points": [
            {
                "title": point.title,
                "reason": point.reason,
                "constraint_ids": point.constraint_ids,
            }
            for point in host_agenda.focus_points
        ],
        "turns": [
            {
                "role": turn.role,
                "task": turn.task,
            }
            for turn in host_agenda.turns
        ],
        "open_questions": list(host_agenda.open_questions),
    }


def _argument_for_prompt(argument: ArgumentOutput) -> dict[str, Any]:
    return {
        "title": argument.title,
        "text": argument.text,
        "claim": argument.claim,
        "evidence": argument.evidence,
        "confidence": argument.confidence,
        "target_claim_ref": argument.target_claim_ref,
    }


def _turn_result_for_prompt(turn_result: SpecialistTurnResult) -> dict[str, Any]:
    return {
        "role": turn_result.specialist.role,
        "display_name": turn_result.specialist.display_name,
        "task": turn_result.task,
        "capability_profile": turn_result.specialist.capability_profile,
        "output": _argument_for_prompt(turn_result.output),
    }


def _route_artifact(route: RoutingDecision) -> dict[str, str]:
    return {
        "tier": route.tier.value,
        "supplier": route.target.supplier,
        "model": route.target.model,
        "reason": route.reason,
    }


def _truncate(text: str, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3].rstrip()}..."


def _dedupe_strings(items: list[str], *, limit: int) -> list[str]:
    seen: list[str] = []
    for item in items:
        normalized = (item or "").strip()
        if normalized and normalized not in seen:
            seen.append(normalized)
        if len(seen) >= limit:
            break
    return seen


def _filter_host_agenda_open_questions(snapshot: Any, host_agenda: HostAgenda) -> HostAgenda:
    filtered_questions = _filter_answered_questions(snapshot, host_agenda.open_questions)
    if filtered_questions == host_agenda.open_questions:
        return host_agenda
    return HostAgenda(
        focus_points=list(host_agenda.focus_points),
        turns=list(host_agenda.turns),
        open_questions=filtered_questions,
        no_new_constraints=host_agenda.no_new_constraints,
    )


def _visible_open_questions(snapshot: Any) -> list[str]:
    return _filter_answered_questions(snapshot, getattr(snapshot, "open_questions", []))


def _filter_answered_questions(snapshot: Any, questions: list[str]) -> list[str]:
    operator_context = _operator_context_from_snapshot(snapshot)
    runtime_context = _runtime_context_from_snapshot(snapshot)
    unresolved: list[str] = []
    for question in questions:
        normalized = str(question).strip()
        if not normalized:
            continue
        if question_answered_by_context(
            normalized,
            runtime_context=runtime_context,
            operator_context=operator_context,
        ):
            continue
        unresolved.append(normalized)
    return _dedupe_strings(unresolved, limit=6)


def _validated_context_for_prompt(snapshot: Any) -> dict[str, Any]:
    operator_context = _generic_operator_context_from_snapshot(snapshot)
    runtime_context = _runtime_context_from_snapshot(snapshot)
    room_start_contract = _room_start_contract_from_snapshot(snapshot)
    context: dict[str, Any] = {}
    if room_start_contract:
        context["room_start_contract"] = room_start_contract
    if operator_context:
        context["operator_context"] = operator_context
    if runtime_context:
        context["runtime_context"] = runtime_context
    known_facts = _known_facts_for_prompt(room_start_contract, runtime_context)
    if known_facts:
        context["known_facts"] = known_facts
    return context


def _known_facts_for_prompt(
    room_start_contract: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
) -> list[str]:
    facts: list[str] = []
    facts.extend(_context_lines("known_context", room_start_contract.get("known_context")))

    planner_target = runtime_context.get("planner_target")
    if isinstance(planner_target, Mapping):
        planner_supplier = str(planner_target.get("supplier", "")).strip()
        planner_model = str(planner_target.get("model", "")).strip()
        if planner_supplier or planner_model:
            facts.append(
                "planner target is "
                + "/".join(item for item in (planner_supplier, planner_model) if item)
            )

    executor_targets = runtime_context.get("executor_targets")
    if isinstance(executor_targets, Mapping):
        for tier in ("default", "escalation", "fallback"):
            target = executor_targets.get(tier)
            if not isinstance(target, Mapping):
                continue
            supplier = str(target.get("supplier", "")).strip()
            model = str(target.get("model", "")).strip()
            if supplier or model:
                facts.append(
                    f"executor {tier} target is "
                    + "/".join(item for item in (supplier, model) if item)
                )
    executor_guardrails = runtime_context.get("executor_guardrails")
    if isinstance(executor_guardrails, Mapping):
        timeout_default = str(
            executor_guardrails.get("request_timeout_default_sec", "")
        ).strip()
        if timeout_default:
            facts.append(f"default provider request timeout is {timeout_default}s")
        provider_timeouts = executor_guardrails.get("provider_timeouts")
        if isinstance(provider_timeouts, Mapping):
            for supplier, payload in provider_timeouts.items():
                if not isinstance(payload, Mapping):
                    continue
                timeout_sec = str(payload.get("timeout_sec", "")).strip()
                if timeout_sec:
                    facts.append(f"{supplier} timeout is {timeout_sec}s")
        transient_attempts = str(
            executor_guardrails.get("transient_max_attempts", "")
        ).strip()
        if transient_attempts:
            facts.append(
                f"provider requests retry on the same route for up to {transient_attempts} attempt(s)"
            )
        fallback_policy = executor_guardrails.get("disaster_fallback_policy")
        if isinstance(fallback_policy, Mapping):
            policy = str(fallback_policy.get("policy", "")).strip()
            max_timeouts = str(
                fallback_policy.get("max_timeouts_before_fallback", "")
            ).strip()
            max_rate_limits = str(
                fallback_policy.get("max_rate_limits_before_fallback", "")
            ).strip()
            if policy or max_timeouts or max_rate_limits:
                facts.append(
                    "disaster fallback policy is "
                    + ", ".join(
                        item
                        for item in (
                            policy or "",
                            f"after {max_timeouts} timeout(s)" if max_timeouts else "",
                            (
                                f"after {max_rate_limits} rate-limit event(s)"
                                if max_rate_limits
                                else ""
                            ),
                        )
                        if item
                    )
                )
        route_visibility = str(executor_guardrails.get("route_visibility", "")).strip()
        if route_visibility:
            facts.append(route_visibility)
    return facts


def _context_lines(label: str, value: Any) -> list[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return [f"{label}: {normalized}"] if normalized else []
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            normalized = str(item).strip()
            if normalized:
                lines.append(f"{label}: {normalized}")
        return lines
    return []


def _operator_context_from_snapshot(snapshot: Any) -> dict[str, Any]:
    planning_artifacts = _planning_artifacts_from_snapshot(snapshot)
    value = planning_artifacts.get("operator_context")
    return dict(value) if isinstance(value, dict) else {}


def _generic_operator_context_from_snapshot(snapshot: Any) -> dict[str, Any]:
    operator_context = _operator_context_from_snapshot(snapshot)
    allowed_keys = {
        "entry_scope",
        "entry_contract",
        "auto_resolved_context",
        "operator_required_inputs",
        "human_control_contract",
    }
    return {
        key: value
        for key, value in operator_context.items()
        if key in allowed_keys
    }


def _room_start_contract_from_snapshot(snapshot: Any) -> dict[str, Any]:
    planning_artifacts = _planning_artifacts_from_snapshot(snapshot)
    value = planning_artifacts.get("room_start_contract")
    return dict(value) if isinstance(value, dict) else {}


def _runtime_context_from_snapshot(snapshot: Any) -> dict[str, Any]:
    planning_artifacts = _planning_artifacts_from_snapshot(snapshot)
    value = planning_artifacts.get("runtime_context")
    return dict(value) if isinstance(value, dict) else {}


def _planning_artifacts_from_snapshot(snapshot: Any) -> dict[str, Any]:
    value = getattr(snapshot, "planning_artifacts", {})
    return value if isinstance(value, dict) else {}


def _require_non_empty_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value.strip()


def _optional_string(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _require_string_list(
    value: object,
    field_name: str,
    *,
    min_items: int,
    max_items: int | None = None,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    items = [str(item).strip() for item in value if str(item).strip()]
    if len(items) < min_items:
        raise ValueError(f"{field_name} must contain at least {min_items} item(s)")
    if max_items is not None and len(items) > max_items:
        raise ValueError(f"{field_name} must contain at most {max_items} item(s)")
    return items


def _require_float(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a float")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a float")
    normalized = float(value)
    if normalized < 0.0 or normalized > 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return normalized


def _coerce_should_end_meeting(
    value: object,
    *,
    conclusion_type: str,
    disagreement: list[str],
    open_questions: list[str],
) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        if conclusion_type in {"blocked", "human_decision_needed"}:
            return True
        if conclusion_type == "candidate_ready" and not disagreement and not open_questions:
            return True
        return False
    raise ValueError("synthesis.should_end_meeting must be a boolean")


def _require_conclusion_type(value: object) -> str:
    normalized = _require_non_empty_str(value, "synthesis.conclusion_type").lower()
    allowed = {
        "candidate_ready",
        "follow_up_required",
        "blocked",
        "human_decision_needed",
    }
    if normalized not in allowed:
        raise ValueError(
            "synthesis.conclusion_type must be one of: "
            + ", ".join(sorted(allowed))
        )
    return normalized


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _env(name: str, env: Mapping[str, str] | None = None) -> str:
    value = os.getenv(name) if env is None else env.get(name)
    if not value:
        raise RuntimeError(f"missing env: {name}")
    return value


def _env_int_optional(name: str, default: int, env: Mapping[str, str] | None = None) -> int:
    value = os.getenv(name) if env is None else env.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"invalid int env: {name}={value}") from exc


def _provider_config(
    supplier: str, env: Mapping[str, str] | None = None
) -> ProviderConfig:
    prefix = supplier.upper()
    timeout_sec = _env_int_optional(
        f"{prefix}_TIMEOUT_SEC",
        _env_int_optional("MODEL_TIMEOUT_SEC", 45, env),
        env,
    )
    return ProviderConfig(
        supplier=supplier,
        base_url=_env(f"{prefix}_BASE_URL", env),
        api_key=_env(f"{prefix}_API_KEY", env),
        timeout_sec=timeout_sec,
    )


def _target(supplier: str, model: str) -> Any:
    return ModelTarget(supplier=supplier, model=model)
