import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from decision_room.mas.hybrid import (
    HybridConsensusStrategy,
    HybridCoordinationStrategy,
    HybridPlanningStrategy,
)
from decision_room.mas.types import DecisionContext, DecisionSignals, MeetingPhase
from decision_room.policies.fallback import DisasterOnlyFallbackPolicy
from decision_room.policies.guardrail import BasicGuardrailPolicy
from decision_room.routing.model_router import HybridModelRouter
from decision_room.runtime.events import EventEnvelope, room_topic


def main() -> None:
    ctx = DecisionContext(
        room_id="room_demo_001",
        phase=MeetingPhase.DEBATE,
        signals=DecisionSignals(
            support=0.62,
            confidence=0.58,
            risk_penalty=0.12,
            margin_top1_top2=0.10,
            disagreement_index=0.52,
            rounds_without_progress=1,
            tool_failure_rate=0.08,
            critical_decision_round=False,
        ),
        metadata={"topic": "MVP multi-agent meeting room"},
    )

    plan = HybridPlanningStrategy().plan(ctx)
    action = HybridCoordinationStrategy().next_action(ctx)
    consensus = HybridConsensusStrategy().evaluate(ctx)
    guardrail = BasicGuardrailPolicy().check(ctx)
    route = HybridModelRouter(DisasterOnlyFallbackPolicy()).route(ctx)

    event = EventEnvelope(
        schema_version="1.0",
        event_id="evt_demo_001",
        room_id=ctx.room_id,
        room_seq=1,
        producer_id="agent.host.1",
        role="host",
        event_type="agent.message",
        ts_ms=1760000000000,
        idempotency_key="room_demo_001:agent.host.1:1",
        payload={"action": action.action_type.value, "reason": action.reason},
    )
    event.validate()

    print("topic:", plan.topic)
    print("next_focus:", plan.next_focus)
    print("action:", action.action_type.value, action.reason)
    print("consensus:", consensus.score, consensus.should_end, consensus.reason)
    print("guardrail:", guardrail.allow, guardrail.escalate, guardrail.reason)
    print("route:", route.tier.value, route.reason)
    print("topic_key:", room_topic(ctx.room_id))


if __name__ == "__main__":
    main()
