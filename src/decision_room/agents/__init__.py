"""Per-role agent classes for the decision-room MAS.

Phase 2 of the native-agent refactor (plan:
``/root/.claude/plans/system-reminder-you-re-running-in-serialized-tiger.md``).

Each agent owns its own prompt builder, parser, retry, and (for
specialists) ReAct iteration loop. The orchestrator (``room_executor`` /
``central_executor``) becomes a thin coordinator that hands a
``TurnContext`` to each agent's ``run`` method and assembles the round
output from what they return.

Surface:

- ``Agent``                 — protocol satisfied by every agent
- ``TurnContext``           — value the orchestrator passes to ``run``
- ``BaseLLMAgent``          — shared retry + disaster-fallback infrastructure
- ``AgentBudgetExhausted``  — raised when ReAct exits without an emit
- ``HostAgent``             — runs the host-led agenda turn
- ``SupervisorAgent``       — runs the supervisor-led speaker-selection turn
- ``SpecialistAgent``       — runs one specialist turn with a ReAct loop
- ``SynthesisAgent``        — runs the per-round synthesis turn
- ``ArgumentOutput``        — final emit-shaped specialist contribution
- ``SpecialistTurnResult``  — per-turn payload returned to the orchestrator
"""

from .base import (
    Agent,
    AgentBudgetExhausted,
    BaseLLMAgent,
    RouteExecution,
    TurnContext,
)
from .host_agent import HostAgent, HostTurnResult
from .specialist_agent import (
    ArgumentOutput,
    ScratchpadEntry,
    SpecialistAgent,
    SpecialistTurnResult,
    build_argument_prompts,
)
from .supervisor_agent import SupervisorAgent, SupervisorTurnResult
from .synthesis_agent import SynthesisAgent, SynthesisTurnResult

__all__ = [
    "Agent",
    "AgentBudgetExhausted",
    "ArgumentOutput",
    "BaseLLMAgent",
    "HostAgent",
    "HostTurnResult",
    "RouteExecution",
    "ScratchpadEntry",
    "SpecialistAgent",
    "SpecialistTurnResult",
    "SupervisorAgent",
    "SupervisorTurnResult",
    "SynthesisAgent",
    "SynthesisTurnResult",
    "TurnContext",
    "build_argument_prompts",
]
