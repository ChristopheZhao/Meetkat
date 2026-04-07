import argparse
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "smoke_real_room_path.py"
SPEC = importlib.util.spec_from_file_location("smoke_real_room_path_script", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load smoke_real_room_path.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _snapshot(
    *,
    room_id: str,
    status: str,
    brief_source: str,
    current_turns: list[dict] | None = None,
    transcript: list[dict] | None = None,
    conclusion_type: str = "",
    conclusion_reason: str = "",
    control_reason: str = "",
    orchestration_end_reason: str = "",
) -> dict:
    return {
        "room_id": room_id,
        "brief_source": brief_source,
        "status": status,
        "phase": "synthesize" if status == "ended" else "explore",
        "round_index": 4 if status == "ended" else 0,
        "current_turns": current_turns or [],
        "transcript": transcript or [],
        "conclusion_type": conclusion_type,
        "conclusion_reason": conclusion_reason,
        "control_reason": control_reason,
        "orchestration_end_reason": orchestration_end_reason,
        "last_seq": 8 if status == "ended" else 2,
    }


def _event(event_type: str) -> dict:
    return {"event_type": event_type}


def _preflight_report(
    *,
    room_start_ready: bool = True,
    missing_operator_inputs: list[str] | None = None,
    contextual_open_questions: list[str] | None = None,
) -> dict:
    return {
        "requirement": "Validate the real room path.",
        "topic": "Validation topic",
        "meeting_objective": "Confirm the room path.",
        "initial_focus": "Check preflight before room start.",
        "constraints": [],
        "open_questions": list(contextual_open_questions or []),
        "brief_source": "agent",
        "brief_source_reason": "",
        "candidate_specialist_roster": [],
        "room_start_contract": {
            "room_start_ready": room_start_ready,
            "runtime_bootstrap_ready": True,
            "missing_operator_inputs": list(missing_operator_inputs or []),
            "contextual_open_questions": list(contextual_open_questions or []),
            "system_blockers": [],
            "known_context": [],
            "recommended_surface": "room_start" if room_start_ready else "operator_input_required",
            "root_cause_hypothesis": "",
        },
        "runtime_readiness": {
            "primary_planner_ready": True,
            "executor_ready": True,
        },
    }


def _message_event(
    *,
    role: str,
    round_index: int,
    phase: str = "synthesize",
    title: str = "",
    artifacts: dict | None = None,
) -> dict:
    return {
        "event_type": "agent.message",
        "role": role,
        "payload": {
            "round_index": round_index,
            "phase": phase,
            "title": title,
            "artifacts": artifacts or {},
        },
    }


def _summary_event(
    *,
    round_index: int,
    conclusion_type: str,
    conclusion_reason: str,
    phase: str = "synthesize",
    artifacts: dict | None = None,
) -> dict:
    return {
        "event_type": "agent.summary",
        "role": "synthesis",
        "payload": {
            "round_index": round_index,
            "phase": phase,
            "conclusion_type": conclusion_type,
            "conclusion_reason": conclusion_reason,
            "artifacts": artifacts or {},
        },
    }


def _consensus_event(
    *,
    score: float,
    should_end: bool,
    reason: str,
    meeting_should_end: bool = False,
    meeting_end_reason: str = "",
) -> dict:
    return {
        "event_type": "consensus.check",
        "role": "system",
        "payload": {
            "score": score,
            "should_end": should_end,
            "meeting_should_end": meeting_should_end,
            "meeting_end_reason": meeting_end_reason,
            "reason": reason,
        },
    }


def _meeting_ended_event(
    *,
    conclusion_type: str,
    conclusion_reason: str,
    control_reason: str,
    orchestration_end_reason: str = "",
) -> dict:
    return {
        "event_type": "meeting.ended",
        "payload": {
            "reason": conclusion_reason,
            "conclusion_type": conclusion_type,
            "conclusion_reason": conclusion_reason,
            "control_reason": control_reason,
            "orchestration_end_reason": orchestration_end_reason,
        },
    }


class FakeRuntime:
    def __init__(
        self,
        *,
        readiness: dict,
        preflight_report: dict | None = None,
        initial_snapshot: dict,
        snapshots: list[dict],
        replay_events: list[dict],
    ) -> None:
        self._readiness = readiness
        self._preflight_report = dict(preflight_report or _preflight_report())
        self._initial_snapshot = initial_snapshot
        self._snapshots = list(snapshots)
        self._replay_events = list(replay_events)
        self._snapshot_index = 0
        self.created: list[dict] = []
        self.preflight_calls: list[dict] = []
        self.closed = False

    def runtime_readiness(self) -> dict:
        return dict(self._readiness)

    def preflight_room(
        self,
        requirement: str,
        *,
        allow_planner_fallback: bool = False,
        entry_scope: str | None = None,
        operator_context: dict | None = None,
    ) -> dict:
        resolved_operator_context = dict(operator_context or {})
        if not resolved_operator_context and entry_scope == getattr(MODULE, "DEFAULT_ENTRY_SCOPE", ""):
            resolved_operator_context = dict(MODULE.DEFAULT_OPERATOR_CONTEXT)
        self.preflight_calls.append(
            {
                "requirement": requirement,
                "allow_planner_fallback": allow_planner_fallback,
                "entry_scope": entry_scope or "",
                "operator_context": resolved_operator_context,
            }
        )
        return dict(self._preflight_report)

    async def create_room(
        self,
        requirement: str,
        mode: str = "agent_first",
        allow_planner_fallback: bool = False,
        require_preflight_ready: bool = False,
        entry_scope: str | None = None,
        operator_context: dict | None = None,
    ) -> dict:
        resolved_operator_context = dict(operator_context or {})
        if not resolved_operator_context and entry_scope == getattr(MODULE, "DEFAULT_ENTRY_SCOPE", ""):
            resolved_operator_context = dict(MODULE.DEFAULT_OPERATOR_CONTEXT)
        self.created.append(
            {
                "requirement": requirement,
                "mode": mode,
                "allow_planner_fallback": allow_planner_fallback,
                "require_preflight_ready": require_preflight_ready,
                "entry_scope": entry_scope or "",
                "operator_context": resolved_operator_context,
            }
        )
        return dict(self._initial_snapshot)

    def get_snapshot(self, room_id: str) -> dict:
        index = min(self._snapshot_index, len(self._snapshots) - 1)
        self._snapshot_index += 1
        return dict(self._snapshots[index])

    def replay(self, room_id: str) -> list[dict]:
        return [dict(event) for event in self._replay_events]

    async def close(self) -> None:
        self.closed = True


class SmokeRealRoomPathScriptTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_real_path_smoke_validates_ready_runtime(self) -> None:
        room_id = "room_real_smoke"
        runtime = FakeRuntime(
            readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
            },
            preflight_report=_preflight_report(),
            initial_snapshot=_snapshot(
                room_id=room_id,
                status="running",
                brief_source="agent",
            ),
            snapshots=[
                _snapshot(room_id=room_id, status="running", brief_source="agent"),
                _snapshot(
                    room_id=room_id,
                    status="ended",
                    brief_source="agent",
                    current_turns=[{"role": "implementation_specialist", "task": "inspect"}],
                    transcript=[{"message_id": "m1", "seq": 5}],
                    conclusion_type="follow_up_required",
                    conclusion_reason="Need external confirmation.",
                ),
            ],
            replay_events=[
                _event("planning.completed"),
                _event("room.started"),
                _message_event(
                    role="host",
                    round_index=1,
                    phase="explore",
                    artifacts={
                        "turns": [{"role": "implementation_specialist", "task": "inspect"}],
                        "route": {
                            "tier": "default",
                            "supplier": "qwen",
                            "model": "qwen-plus",
                            "reason": "baseline",
                        },
                    },
                ),
                _message_event(
                    role="implementation_specialist",
                    round_index=1,
                    artifacts={
                        "turn_index": 1,
                        "route": {
                            "tier": "escalation",
                            "supplier": "glm",
                            "model": "glm-4.5",
                            "reason": "challenge path",
                        },
                    },
                ),
                _summary_event(
                    round_index=1,
                    conclusion_type="follow_up_required",
                    conclusion_reason="Need external confirmation.",
                    artifacts={
                        "route": {
                            "tier": "escalation",
                            "supplier": "qwen",
                            "model": "qwen-plus",
                            "reason": "synthesis escalation",
                        }
                    },
                ),
                _consensus_event(
                    score=0.66,
                    should_end=True,
                    reason="ready to close",
                    meeting_should_end=True,
                    meeting_end_reason="synthesis marked the room ready to close",
                ),
                _meeting_ended_event(
                    conclusion_type="follow_up_required",
                    conclusion_reason="Need external confirmation.",
                    control_reason="control gate accepted orchestration end signal",
                    orchestration_end_reason="consensus threshold reached",
                ),
            ],
        )

        result = await MODULE.run_real_path_smoke(
            runtime,
            requirement="Validate the real room path.",
            timeout_sec=1.0,
            poll_interval_sec=0.0,
        )

        self.assertEqual(result["room_id"], room_id)
        self.assertEqual(result["conclusion_type"], "follow_up_required")
        self.assertEqual(result["status"], "ended")
        self.assertEqual(runtime.created[0]["allow_planner_fallback"], False)
        self.assertEqual(runtime.created[0]["require_preflight_ready"], True)
        self.assertEqual(runtime.created[0]["entry_scope"], MODULE.DEFAULT_ENTRY_SCOPE)
        self.assertEqual(runtime.created[0]["operator_context"]["brief_source"], "agent")
        self.assertTrue(runtime.created[0]["operator_context"]["validation_scenario"])
        self.assertTrue(runtime.created[0]["operator_context"]["binding_readiness_contract"])
        self.assertTrue(runtime.created[0]["operator_context"]["transport_contract"])
        self.assertTrue(runtime.created[0]["operator_context"]["projection_contract"])
        self.assertEqual(runtime.preflight_calls[0]["operator_context"]["brief_source"], "agent")
        self.assertEqual(runtime.preflight_calls[0]["entry_scope"], MODULE.DEFAULT_ENTRY_SCOPE)
        self.assertEqual(result["room_start_contract"]["room_start_ready"], True)
        self.assertEqual(
            result["meeting_end"]["control_reason"],
            "control gate accepted orchestration end signal",
        )
        self.assertEqual(
            result["meeting_end"]["orchestration_end_reason"],
            "consensus threshold reached",
        )
        self.assertEqual(result["event_type_counts"]["agent.message"], 2)
        self.assertEqual(result["round_diagnostics"][0]["host_route"]["supplier"], "qwen")
        self.assertEqual(
            result["round_diagnostics"][0]["specialist_routes"][0]["route"]["supplier"],
            "glm",
        )
        self.assertEqual(
            result["round_diagnostics"][0]["synthesis_route"]["reason"],
            "synthesis escalation",
        )
        self.assertTrue(result["round_diagnostics"][0]["consensus"]["meeting_should_end"])
        self.assertEqual(
            result["round_diagnostics"][0]["consensus"]["meeting_end_reason"],
            "synthesis marked the room ready to close",
        )

    async def test_run_real_path_smoke_rejects_unready_runtime(self) -> None:
        runtime = FakeRuntime(
            readiness={
                "primary_planner_ready": False,
                "executor_ready": True,
                "primary_unavailable_reason": "missing planner env",
                "executor_reason": "",
            },
            preflight_report=_preflight_report(),
            initial_snapshot=_snapshot(
                room_id="room_blocked",
                status="running",
                brief_source="agent",
            ),
            snapshots=[],
            replay_events=[],
        )

        with self.assertRaisesRegex(RuntimeError, "ready primary planner"):
            await MODULE.run_real_path_smoke(
                runtime,
                requirement="Validate the real room path.",
                timeout_sec=1.0,
                poll_interval_sec=0.0,
            )

    async def test_execute_real_path_smoke_closes_runtime(self) -> None:
        room_id = "room_execute"
        runtime = FakeRuntime(
            readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
            },
            preflight_report=_preflight_report(),
            initial_snapshot=_snapshot(
                room_id=room_id,
                status="running",
                brief_source="agent",
            ),
            snapshots=[
                _snapshot(
                    room_id=room_id,
                    status="ended",
                    brief_source="agent",
                    current_turns=[{"role": "risk_specialist", "task": "challenge"}],
                    transcript=[{"message_id": "m2", "seq": 8}],
                    conclusion_type="follow_up_required",
                    conclusion_reason="Need provider-specific input.",
                )
            ],
            replay_events=[
                _event("planning.completed"),
                _event("room.started"),
                _message_event(
                    role="host",
                    round_index=1,
                    artifacts={
                        "turns": [{"role": "risk_specialist", "task": "challenge"}],
                        "route": {
                            "tier": "default",
                            "supplier": "qwen",
                            "model": "qwen-plus",
                            "reason": "baseline",
                        },
                    },
                ),
                _summary_event(
                    round_index=1,
                    conclusion_type="follow_up_required",
                    conclusion_reason="Need provider-specific input.",
                    artifacts={
                        "route": {
                            "tier": "default",
                            "supplier": "qwen",
                            "model": "qwen-plus",
                            "reason": "stable synthesis",
                        }
                    },
                ),
                _consensus_event(
                    score=0.61,
                    should_end=True,
                    reason="close",
                    meeting_should_end=True,
                    meeting_end_reason="synthesis marked the room ready to close",
                ),
                _meeting_ended_event(
                    conclusion_type="follow_up_required",
                    conclusion_reason="Need provider-specific input.",
                    control_reason="control gate accepted orchestration end signal",
                    orchestration_end_reason="consensus threshold reached",
                ),
            ],
        )
        args = argparse.Namespace(
            requirement="Validate the real room path.",
            timeout_sec=1.0,
            poll_interval_sec=0.0,
            max_rounds=4,
            json=True,
            allow_preflight_blocked=False,
        )

        with mock.patch.object(MODULE, "build_runtime_from_env", return_value=runtime):
            result = await MODULE.execute_real_path_smoke(args)

        self.assertTrue(runtime.closed)
        self.assertEqual(result["room_id"], room_id)

    async def test_run_real_path_smoke_classifies_blocked_external_dependencies(self) -> None:
        room_id = "room_blocked_diag"
        runtime = FakeRuntime(
            readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
            },
            preflight_report=_preflight_report(),
            initial_snapshot=_snapshot(
                room_id=room_id,
                status="running",
                brief_source="agent",
            ),
            snapshots=[
                _snapshot(
                    room_id=room_id,
                    status="ended",
                    brief_source="agent",
                    current_turns=[{"role": "operations_specialist", "task": "diagnose"}],
                    transcript=[{"message_id": "m3", "seq": 9}],
                    conclusion_type="blocked",
                    conclusion_reason=(
                        "Execution cannot begin without provider identity, success criteria, "
                        "and credential context."
                    ),
                    control_reason="control gate accepted orchestration end signal",
                    orchestration_end_reason=(
                        "Execution cannot begin without provider identity, success criteria, "
                        "and credential context."
                    ),
                )
            ],
            replay_events=[
                _event("planning.completed"),
                _event("room.started"),
                _message_event(
                    role="host",
                    round_index=1,
                    artifacts={
                        "turns": [{"role": "operations_specialist", "task": "diagnose"}],
                    },
                ),
                _summary_event(
                    round_index=1,
                    conclusion_type="blocked",
                    conclusion_reason=(
                        "Execution cannot begin without provider identity, success criteria, "
                        "and credential context."
                    ),
                ),
                _consensus_event(
                    score=0.42,
                    should_end=False,
                    reason="continue meeting",
                    meeting_should_end=True,
                    meeting_end_reason=(
                        "Execution cannot begin without provider identity, success criteria, "
                        "and credential context."
                    ),
                ),
                _meeting_ended_event(
                    conclusion_type="blocked",
                    conclusion_reason=(
                        "Execution cannot begin without provider identity, success criteria, "
                        "and credential context."
                    ),
                    control_reason="control gate accepted orchestration end signal",
                    orchestration_end_reason=(
                        "Execution cannot begin without provider identity, success criteria, "
                        "and credential context."
                    ),
                ),
            ],
        )

        result = await MODULE.run_real_path_smoke(
            runtime,
            requirement="Diagnose blocked external dependencies.",
            timeout_sec=1.0,
            poll_interval_sec=0.0,
        )

        analysis = result["blocked_dependency_analysis"]
        self.assertTrue(analysis["has_blocked_dependencies"])
        self.assertEqual(analysis["recommended_surface"], "operator_preflight")
        self.assertEqual(
            [item["id"] for item in analysis["categories"]],
            ["provider_identity", "success_criteria", "credential_context"],
        )
        self.assertIn("preflight gate", analysis["root_cause_hypothesis"])

    async def test_run_real_path_smoke_blocks_before_room_start_when_preflight_not_ready(self) -> None:
        runtime = FakeRuntime(
            readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
            },
            preflight_report=_preflight_report(
                room_start_ready=False,
                missing_operator_inputs=["Which specific provider is required for this validation?"],
            ),
            initial_snapshot=_snapshot(
                room_id="room_never_created",
                status="running",
                brief_source="agent",
            ),
            snapshots=[],
            replay_events=[],
        )

        with self.assertRaisesRegex(RuntimeError, "blocked by room-start contract gaps"):
            await MODULE.run_real_path_smoke(
                runtime,
                requirement="Validate the real room path.",
                timeout_sec=1.0,
                poll_interval_sec=0.0,
            )

        self.assertEqual(len(runtime.preflight_calls), 1)
        self.assertEqual(runtime.created, [])

    async def test_run_real_path_smoke_can_override_preflight_gate_for_blocked_diagnostics(self) -> None:
        room_id = "room_override_blocked"
        runtime = FakeRuntime(
            readiness={
                "primary_planner_ready": True,
                "executor_ready": True,
                "primary_unavailable_reason": "",
                "executor_reason": "",
            },
            preflight_report=_preflight_report(
                room_start_ready=False,
                missing_operator_inputs=["Which specific provider is required for this validation?"],
            ),
            initial_snapshot=_snapshot(
                room_id=room_id,
                status="running",
                brief_source="agent",
            ),
            snapshots=[
                _snapshot(
                    room_id=room_id,
                    status="ended",
                    brief_source="agent",
                    current_turns=[{"role": "operations_specialist", "task": "diagnose"}],
                    transcript=[{"message_id": "m4", "seq": 10}],
                    conclusion_type="blocked",
                    conclusion_reason="Need specific provider confirmation.",
                )
            ],
            replay_events=[
                _event("planning.completed"),
                _event("room.started"),
                _summary_event(
                    round_index=1,
                    conclusion_type="blocked",
                    conclusion_reason="Need specific provider confirmation.",
                ),
                _meeting_ended_event(
                    conclusion_type="blocked",
                    conclusion_reason="Need specific provider confirmation.",
                    control_reason="control gate accepted orchestration end signal",
                    orchestration_end_reason="Need specific provider confirmation.",
                ),
            ],
        )

        result = await MODULE.run_real_path_smoke(
            runtime,
            requirement="Diagnose blocked external dependencies.",
            timeout_sec=1.0,
            poll_interval_sec=0.0,
            allow_preflight_blocked=True,
        )

        self.assertEqual(result["room_start_contract"]["room_start_ready"], False)
        self.assertEqual(runtime.created[0]["require_preflight_ready"], False)
        self.assertEqual(runtime.created[0]["entry_scope"], MODULE.DEFAULT_ENTRY_SCOPE)
        self.assertEqual(runtime.created[0]["operator_context"]["brief_source"], "agent")
        self.assertTrue(runtime.created[0]["operator_context"]["validation_scenario"])
        self.assertTrue(runtime.created[0]["operator_context"]["binding_readiness_contract"])
        self.assertTrue(runtime.created[0]["operator_context"]["transport_contract"])
        self.assertTrue(runtime.created[0]["operator_context"]["projection_contract"])


if __name__ == "__main__":
    unittest.main()
