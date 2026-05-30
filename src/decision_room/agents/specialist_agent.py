"""Specialist agent — owns the ReAct iteration loop and tool use.

Phase 2 of the native-agent refactor. Before Phase 2 a "specialist" was
just a function: ``LLMRoomExecutor._generate_argument`` built a prompt,
ran one LLM call, parsed JSON, and returned. There was no loop, no tool
seam, no agency. ``CandidateSpecialist.turn_budget`` carried a number
through the system but nothing ever read it.

After Phase 2 each specialist is a ``SpecialistAgent`` with its own
think → optional tool call → reflect → emit loop, bounded by the
specialist's ``turn_budget``. Tools dispatched here emit
``agent.tool_call`` / ``agent.tool_result`` events through the
``RoomEventJournal`` (when a publish callback is wired), and tool
results land in the agent's local scratchpad for the next iteration to
consume.

Backward compatibility: when the LLM returns a response that does not
contain a ``next_action`` field, the agent treats it as an implicit
``emit``. Pre-Phase-2 providers (and tests) keep working unchanged.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from decision_room.mas.types import MeetingPhase, RoutingDecision
from decision_room.providers import ProviderRegistry
from decision_room.routing.model_router import HybridModelRouter
from decision_room.tools import ToolRegistry, ToolResult

from .base import (
    AgentBudgetExhausted,
    BaseLLMAgent,
    RouteExecution,
    TurnContext,
)


_VALID_NEXT_ACTIONS = {"emit", "think", "call_tool"}
_REACT_PROMPT_BUDGET_THRESHOLD = 2

# Re-export the canonical ArgumentOutput from room_executor so the agent
# module returns the exact class the orchestrator's SpecialistTurnResult
# expects. Two classes with identical shape would work at runtime (Python
# doesn't enforce dataclass field types) but quickly drifts under
# refactor.
from decision_room.orchestration.room_executor import (  # noqa: E402
    ArgumentOutput as ArgumentOutput,
)


@dataclass(frozen=True)
class ScratchpadEntry:
    """One ReAct iteration's intermediate state.

    ``kind`` is "think" (model reasoning) or "tool" (registered tool
    result). Persisted into the next iteration's prompt under
    ``scratchpad`` so the model can build on its prior steps.
    """

    iteration: int
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {"iteration": self.iteration, "kind": self.kind, **self.payload}


@dataclass(frozen=True)
class SpecialistTurnResult:
    """Per-turn output handed back to the orchestrator."""

    output: ArgumentOutput
    route: RoutingDecision
    ctx: Any  # DecisionContext — Any to avoid cycle
    iterations: int
    scratchpad: list[ScratchpadEntry] = field(default_factory=list)


@dataclass(frozen=True)
class _ReactDecision:
    next_action: str
    output: ArgumentOutput | None = None
    thought: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)


class SpecialistAgent(BaseLLMAgent):
    """One specialist role running as a real agent with its own loop."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        router: HybridModelRouter,
        tool_registry: ToolRegistry | None = None,
        use_background_threads: bool = True,
        transient_max_attempts: int = 2,
    ) -> None:
        super().__init__(
            registry=registry,
            router=router,
            use_background_threads=use_background_threads,
            transient_max_attempts=transient_max_attempts,
        )
        self._tool_registry = tool_registry

    @property
    def role(self) -> str:
        return "specialist"

    async def run(self, ctx: TurnContext) -> SpecialistTurnResult:
        extras = ctx.extras
        specialist = extras["specialist"]
        turn_task = str(extras.get("turn_task", "") or "")
        host_agenda = extras["host_agenda"]
        target_claim_ref = str(extras.get("target_claim_ref", "") or "")
        memory_recall = extras.get("memory_recall")
        budget = max(1, int(getattr(specialist, "turn_budget", 1) or 1))
        scratchpad: list[ScratchpadEntry] = []
        last_action = "none"
        route_ctx = ctx.route_ctx
        route = ctx.route

        for iteration in range(1, budget + 1):
            system_prompt, user_prompt = build_argument_prompts(
                specialist=specialist,
                turn_task=turn_task,
                snapshot=ctx.snapshot,
                phase=ctx.phase,
                round_index=ctx.round_index,
                next_focus=ctx.next_focus,
                host_agenda=host_agenda,
                target_claim_ref=target_claim_ref,
                route=route,
                memory_recall=memory_recall,
                turn_budget=budget,
                scratchpad=scratchpad,
                tool_descriptors=self._available_tool_descriptors(),
            )
            execution: RouteExecution = await self.generate_text(
                route_ctx=route_ctx,
                route=route,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                role=specialist.role,
            )
            route_ctx = execution.ctx
            route = execution.route
            decision = _parse_react_response(execution.text, role=specialist.role)
            last_action = decision.next_action

            if decision.next_action == "emit" and decision.output is not None:
                return SpecialistTurnResult(
                    output=decision.output,
                    route=route,
                    ctx=route_ctx,
                    iterations=iteration,
                    scratchpad=list(scratchpad),
                )

            if decision.next_action == "think":
                scratchpad.append(
                    ScratchpadEntry(
                        iteration=iteration,
                        kind="think",
                        payload={"thought": decision.thought},
                    )
                )
                continue

            if decision.next_action == "call_tool":
                tool_result = await self._dispatch_tool(
                    ctx=ctx,
                    specialist=specialist,
                    tool_name=decision.tool_name,
                    tool_args=decision.tool_args,
                )
                scratchpad.append(
                    ScratchpadEntry(
                        iteration=iteration,
                        kind="tool",
                        payload={
                            "tool_name": decision.tool_name,
                            "args": dict(decision.tool_args),
                            "ok": tool_result.ok,
                            "result": tool_result.result,
                            "error": tool_result.error,
                            "latency_ms": tool_result.latency_ms,
                        },
                    )
                )
                continue

            # Unknown next_action: terminate the loop defensively — the
            # parser will only emit values from _VALID_NEXT_ACTIONS, so
            # reaching here means a future schema slipped past it.
            raise AgentBudgetExhausted(
                role=specialist.role,
                budget=budget,
                last_action=decision.next_action or "unknown",
            )

        raise AgentBudgetExhausted(
            role=specialist.role, budget=budget, last_action=last_action
        )

    def _available_tool_descriptors(self) -> list[dict[str, Any]]:
        if self._tool_registry is None:
            return []
        return [
            descriptor.to_payload()
            for descriptor in self._tool_registry.describe()
        ]

    async def _dispatch_tool(
        self,
        *,
        ctx: TurnContext,
        specialist: Any,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> ToolResult:
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        producer_id = f"agent.{specialist.role}.1"
        if ctx.publish is not None:
            await ctx.publish(
                ctx.snapshot.room_id,
                producer_id=producer_id,
                role=specialist.role,
                event_type="agent.tool_call",
                payload={
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "agent_role": specialist.role,
                    "args": dict(tool_args),
                },
            )
        if self._tool_registry is None:
            result = ToolResult(
                ok=False,
                error="no tool registry wired into agent",
                latency_ms=0,
            )
        else:
            result = self._tool_registry.dispatch(tool_name, tool_args)
        if ctx.publish is not None:
            await ctx.publish(
                ctx.snapshot.room_id,
                producer_id=producer_id,
                role=specialist.role,
                event_type="agent.tool_result",
                payload={
                    "call_id": call_id,
                    "ok": result.ok,
                    "result": result.result,
                    "error": result.error,
                    "latency_ms": result.latency_ms,
                },
            )
        return result


def build_argument_prompts(
    *,
    specialist: Any,
    turn_task: str,
    snapshot: Any,
    phase: MeetingPhase,
    round_index: int,
    next_focus: str,
    host_agenda: Any,
    target_claim_ref: str,
    route: RoutingDecision,
    memory_recall: dict[str, Any] | None = None,
    turn_budget: int = 1,
    scratchpad: list[ScratchpadEntry] | None = None,
    tool_descriptors: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for one specialist turn.

    Backward-compatible: when ``turn_budget <= 1`` and no tools are
    registered, the emitted prompts are byte-identical to the pre-Phase-2
    output of ``room_executor._build_argument_prompts``. Pre-Phase-2
    providers and tests stay green. When ``turn_budget >= 2`` or tools are
    available, an extra ReAct section is appended after the schema example.
    """
    # Local import to avoid a module-level cycle: room_executor imports
    # from agents.specialist_agent, and these prompt-shaping helpers live
    # in room_executor.
    from decision_room.orchestration.room_executor import (
        _host_agenda_for_prompt,
        _room_state_for_prompt,
        _route_artifact,
        _specialist_for_prompt,
    )

    schema = {
        "title": "short title",
        "text": "1-3 concise paragraphs",
        "claim": "main position",
        "evidence": ["supporting point"],
        "confidence": 0.72,
        "target_claim_ref": "claim being supported or challenged",
    }
    turn_mode = (
        "respond to a visible prior claim"
        if target_claim_ref
        else "introduce a grounded specialist contribution"
    )
    system_prompt = (
        f"You are the {specialist.display_name} in a multi-agent decision room. "
        f"Your capability profile is: {specialist.capability_profile} "
        "You are an autonomous specialist agent. The supervisor has decided WHO "
        "speaks WHEN and may have offered an optional `focus_angle` hint — but "
        "you are the author of WHAT this round's contribution is. Decide your "
        "own claim, evidence, and confidence based on your role contract, the "
        "room state, your memory recall, and the round's decision focus. Do "
        f"not wait to be told what to say. Your job this turn is to {turn_mode}. "
        "Stay grounded in the room brief, recent agenda, and visible blackboard state. "
        "Treat room_state.validated_context as already resolved input rather than an open question surface. "
        "Do not invent constraints or hidden requirements. "
        "Clarification protocol: if a material ambiguity in the requirement or "
        "in the supervisor's focus would meaningfully change your claim (a "
        "missing definition, an unstated scope boundary, a needed assumption "
        "you have no basis for), do not fabricate. Ask the operator one "
        "focused clarifying question in your `text` field, set `claim` to "
        "start with '[Awaiting operator clarification]' followed by the same "
        "one-line question, set `confidence` to 0.30 or lower, and provide "
        "your best partial reasoning in `evidence`. The operator answers via "
        "the room human-message channel and the answer appears as "
        "`room_state.last_human_message` next round so you can complete the "
        "analysis. Use this protocol sparingly — only when guessing would "
        "distort the decision record. "
        "Return exactly one JSON object and nothing else."
    )
    focus_angle_line = (
        f"- Supervisor focus_angle hint (optional, may be empty — ignore if it does not improve your contribution): {turn_task}\n"
        if turn_task
        else "- The supervisor did not provide a focus_angle hint. Your role contract is your starting frame.\n"
    )
    memory_section = ""
    if memory_recall:
        memory_section = (
            "Memory recall (shared room scratchpad + your role-specific lessons + recent shared events):\n"
            f"{json.dumps(memory_recall, ensure_ascii=False, indent=2)}\n\n"
        )
    user_prompt = (
        "Room state:\n"
        f"{json.dumps(_room_state_for_prompt(snapshot, phase, round_index, next_focus), ensure_ascii=False, indent=2)}\n\n"
        f"{memory_section}"
        "Host agenda (for context — your contribution is NOT prescribed by this):\n"
        f"{json.dumps(_host_agenda_for_prompt(host_agenda), ensure_ascii=False, indent=2)}\n\n"
        "Routing info:\n"
        f"{json.dumps(_route_artifact(route), ensure_ascii=False, indent=2)}\n\n"
        "Specialist profile (your role contract — your authoritative framing):\n"
        f"{json.dumps(_specialist_for_prompt(specialist), ensure_ascii=False, indent=2)}\n\n"
        "Task:\n"
        "- You are the author of this round's contribution from your role's perspective. The supervisor only set the speaking order; what you say is your call.\n"
        + focus_angle_line
        + f"- Stay within the {specialist.role} specialist remit; your role contract is the boundary, not a script.\n"
        "- Keep the message concise and operator-readable.\n"
        "- Extract one clear claim.\n"
        "- Provide 2-4 factual evidence bullets.\n"
        "- Emit confidence as a float between 0 and 1, calibrated to how strongly your evidence supports the claim.\n"
        f"- target_claim_ref should be {json.dumps(target_claim_ref)} if provided, otherwise use an empty string.\n"
        "- If target_claim_ref is present, respond directly to that visible claim instead of starting a disconnected thread.\n\n"
        "Output schema example:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )

    if turn_budget >= _REACT_PROMPT_BUDGET_THRESHOLD or tool_descriptors:
        user_prompt += _react_loop_addendum(
            turn_budget=turn_budget,
            tool_descriptors=tool_descriptors or [],
            scratchpad=scratchpad or [],
        )

    return system_prompt, user_prompt


def _react_loop_addendum(
    *,
    turn_budget: int,
    tool_descriptors: list[dict[str, Any]],
    scratchpad: list[ScratchpadEntry],
) -> str:
    """Render the optional ReAct + tools instructions appended to the
    specialist prompt when the agent has > 1 iteration or has tools wired.
    """
    tool_lines = "\n".join(
        f"  - {item.get('name', '')}: {item.get('description', '')}"
        for item in tool_descriptors
        if item.get("name")
    )
    tools_block = (
        f"Available tools (call by name from `tool_call.name`):\n{tool_lines}\n\n"
        if tool_lines
        else "No tools are currently registered. Use next_action='think' or 'emit'.\n\n"
    )
    scratchpad_payload = json.dumps(
        [entry.to_payload() for entry in scratchpad],
        ensure_ascii=False,
        indent=2,
    )
    react_schema = {
        "next_action": "emit | think | call_tool",
        "thought": "OPTIONAL reasoning when next_action=think",
        "tool_call": {
            "name": "OPTIONAL tool name when next_action=call_tool",
            "args": {"key": "value"},
        },
        "title": "REQUIRED when next_action=emit (same shape as the schema above)",
        "text": "REQUIRED when next_action=emit",
        "claim": "REQUIRED when next_action=emit",
        "evidence": ["REQUIRED when next_action=emit"],
        "confidence": "REQUIRED when next_action=emit",
        "target_claim_ref": "REQUIRED when next_action=emit",
    }
    return (
        "\n\nReAct loop (active because you have a multi-step budget or tools available):\n"
        f"- You may iterate up to {turn_budget} step(s) before emitting your final contribution.\n"
        "- next_action='emit' delivers the final ArgumentOutput. Use it when you have enough evidence.\n"
        "- next_action='think' lets you record one reasoning step in `thought`. The next iteration's prompt will include your prior thoughts.\n"
        "- next_action='call_tool' invokes one registered tool with {name, args}. The next iteration's prompt will include the tool's result.\n"
        "- Omit `next_action` (or set it to 'emit') for a single-shot turn. Default is always emit.\n\n"
        f"{tools_block}"
        f"Scratchpad (your prior steps this turn — empty on iteration 1):\n{scratchpad_payload}\n\n"
        "ReAct schema example (only the fields relevant to your chosen next_action are required):\n"
        f"{json.dumps(react_schema, ensure_ascii=False, indent=2)}"
    )


def _parse_react_response(raw: str, *, role: str) -> _ReactDecision:
    """Parse one specialist LLM response, supporting both legacy emit-only
    payloads and the ReAct schema. Missing ``next_action`` defaults to
    'emit' so pre-Phase-2 providers/tests keep working.
    """
    # Local import to avoid cycle through orchestration package.
    from decision_room.orchestration.room_executor import (
        _load_json_payload,
        _parse_argument_output,
    )

    payload = _load_json_payload(raw, role)
    next_action_raw = payload.get("next_action")
    if next_action_raw is None:
        next_action = "emit"
    else:
        next_action = str(next_action_raw).strip().lower().replace("-", "_")
        if next_action not in _VALID_NEXT_ACTIONS:
            next_action = "emit"

    if next_action == "think":
        thought = str(payload.get("thought", "") or "").strip()
        if not thought:
            raise ValueError(
                f"{role}: next_action='think' requires a non-empty 'thought' field"
            )
        return _ReactDecision(next_action="think", thought=thought)

    if next_action == "call_tool":
        tool_call = payload.get("tool_call")
        if not isinstance(tool_call, dict):
            raise ValueError(
                f"{role}: next_action='call_tool' requires a tool_call object"
            )
        tool_name = str(tool_call.get("name", "") or "").strip()
        if not tool_name:
            raise ValueError(
                f"{role}: next_action='call_tool' requires tool_call.name"
            )
        tool_args = tool_call.get("args")
        if tool_args is None:
            tool_args = {}
        if not isinstance(tool_args, dict):
            raise ValueError(
                f"{role}: tool_call.args must be an object when provided"
            )
        return _ReactDecision(
            next_action="call_tool",
            tool_name=tool_name,
            tool_args=dict(tool_args),
        )

    output = _parse_argument_output(raw, role=role)
    return _ReactDecision(next_action="emit", output=output)
