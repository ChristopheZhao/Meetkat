import asyncio
import unittest
from unittest import mock

from decision_room.runtime.http_api import build_runtime_from_env
from decision_room.runtime.room_runtime import RuntimeConfig


class HttpApiRuntimeBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            await runtime.close()

    async def _wait_until(self, predicate, timeout: float = 1.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("timed out waiting for room state transition")

    async def test_build_runtime_from_env_supports_demo_executor_and_fallback_planner(
        self,
    ) -> None:
        self.runtime = build_runtime_from_env(
            {
                "DECISION_ROOM_EXECUTOR": "demo",
                "DECISION_ROOM_PLANNER_MODE": "fallback_only",
            },
            config=RuntimeConfig(
                message_chunk_delay_sec=0.0,
                between_turn_delay_sec=0.0,
                between_round_delay_sec=0.0,
                max_rounds=4,
            ),
        )

        snapshot = await self.runtime.create_room(
            requirement=(
                "Need a browser-verifiable room that exposes dynamic turns and explicit "
                "conclusion semantics."
            ),
            allow_planner_fallback=True,
        )

        self.assertEqual(snapshot["brief_source"], "fallback")
        self.assertIn("explicit fallback", snapshot["brief_source_reason"])

        await self._wait_until(
            lambda: self.runtime.get_snapshot(snapshot["room_id"])["status"] == "ended"
        )

        current = self.runtime.get_snapshot(snapshot["room_id"])
        self.assertGreater(len(current["current_turns"]), 0)
        self.assertEqual(current["conclusion_type"], "follow_up_required")
        self.assertIn("follow-up", current["conclusion_reason"].lower())

    async def test_build_runtime_from_env_exposes_preflight_payload(self) -> None:
        self.runtime = build_runtime_from_env(
            {
                "DECISION_ROOM_EXECUTOR": "demo",
                "DECISION_ROOM_PLANNER_MODE": "fallback_only",
            },
            config=RuntimeConfig(),
        )

        preflight = self.runtime.preflight_room(
            requirement="Need a browser-verifiable room that exposes dynamic turns and explicit conclusion semantics.",
            allow_planner_fallback=True,
        )

        self.assertIn("room_start_contract", preflight)
        self.assertIn("runtime_readiness", preflight)
        self.assertIn("contextual_open_questions", preflight["room_start_contract"])
        self.assertTrue(preflight["room_start_contract"]["room_start_ready"])

    async def test_build_runtime_from_env_autoloads_local_dotenv_only_when_env_unspecified(
        self,
    ) -> None:
        with mock.patch("decision_room.runtime.http_api.load_local_dotenv") as load_dotenv:
            self.runtime = build_runtime_from_env({}, config=RuntimeConfig())
            load_dotenv.assert_not_called()

        await self.runtime.close()
        self.runtime = None

        with mock.patch("decision_room.runtime.http_api.load_local_dotenv") as load_dotenv:
            self.runtime = build_runtime_from_env(config=RuntimeConfig())
            load_dotenv.assert_called_once()

    async def test_build_runtime_from_env_defaults_to_llm_topology_unavailable_without_env(
        self,
    ) -> None:
        self.runtime = build_runtime_from_env({}, config=RuntimeConfig())

        readiness = self.runtime.runtime_readiness()
        self.assertEqual(readiness["planner_mode"], "primary_with_fallback")
        self.assertEqual(readiness["executor_mode"], "llm")
        self.assertFalse(readiness["primary_planner_ready"])
        self.assertFalse(readiness["executor_ready"])
        self.assertIn("MODEL_DEFAULT_SUPPLIER", readiness["executor_missing_env"])

    async def test_build_runtime_from_env_centralized_mode_requires_provider_env(self) -> None:
        self.runtime = build_runtime_from_env(
            {"DECISION_ROOM_EXECUTOR": "centralized"},
            config=RuntimeConfig(),
        )

        readiness = self.runtime.runtime_readiness()
        self.assertEqual(readiness["executor_mode"], "centralized")
        self.assertFalse(readiness["executor_ready"])
        self.assertTrue(str(readiness["executor_reason"]).strip())
        self.assertIn("MODEL_DEFAULT_SUPPLIER", readiness["executor_missing_env"])
        self.assertEqual(
            readiness["executor_guardrails"]["topology"],
            "single_supervisor_shared_memory",
        )

    async def test_build_runtime_from_env_exposes_provider_targets_in_readiness(self) -> None:
        self.runtime = build_runtime_from_env(
            {
                "DECISION_ROOM_EXECUTOR": "llm",
                "DECISION_ROOM_PLANNER_MODE": "primary",
                "MODEL_DEFAULT_SUPPLIER": "openai",
                "MODEL_DEFAULT_MODEL": "gpt-default",
                "MODEL_ESCALATION_SUPPLIER": "openai",
                "MODEL_ESCALATION_MODEL": "gpt-escalation",
                "MODEL_FALLBACK_SUPPLIER": "openai",
                "MODEL_FALLBACK_MODEL": "gpt-fallback",
                "OPENAI_BASE_URL": "https://example.com/v1",
                "OPENAI_API_KEY": "test-key",
            },
            config=RuntimeConfig(),
        )

        readiness = self.runtime.runtime_readiness()

        self.assertEqual(
            readiness["planner_target"],
            {"supplier": "openai", "model": "gpt-default"},
        )
        self.assertEqual(
            readiness["executor_targets"]["default"],
            {"supplier": "openai", "model": "gpt-default"},
        )
        self.assertEqual(
            readiness["executor_targets"]["escalation"],
            {"supplier": "openai", "model": "gpt-escalation"},
        )
        self.assertEqual(
            readiness["executor_targets"]["fallback"],
            {"supplier": "openai", "model": "gpt-fallback"},
        )
        self.assertEqual(readiness["executor_guardrails"]["request_timeout_default_sec"], 45)
        self.assertEqual(
            readiness["executor_guardrails"]["provider_timeouts"]["openai"]["timeout_sec"],
            45,
        )
        self.assertEqual(
            readiness["executor_guardrails"]["disaster_fallback_policy"]["policy"],
            "disaster_only",
        )

    async def test_build_runtime_from_env_exposes_real_path_readiness_blockers(self) -> None:
        self.runtime = build_runtime_from_env(
            {
                "DECISION_ROOM_EXECUTOR": "llm",
                "DECISION_ROOM_PLANNER_MODE": "primary",
            },
            config=RuntimeConfig(),
        )

        readiness = self.runtime.runtime_readiness()

        self.assertEqual(readiness["planner_mode"], "primary")
        self.assertFalse(readiness["primary_planner_ready"])
        self.assertFalse(readiness["executor_ready"])
        self.assertTrue(str(readiness["primary_unavailable_reason"]).strip())
        self.assertTrue(str(readiness["executor_reason"]).strip())
        self.assertIn("MODEL_DEFAULT_SUPPLIER", readiness["planner_missing_env"])
        self.assertIn("MODEL_DEFAULT_MODEL", readiness["planner_missing_env"])
        self.assertIn("MODEL_DEFAULT_SUPPLIER", readiness["executor_missing_env"])
        self.assertIn("MODEL_ESCALATION_SUPPLIER", readiness["executor_missing_env"])
        self.assertIn("MODEL_FALLBACK_SUPPLIER", readiness["executor_missing_env"])
