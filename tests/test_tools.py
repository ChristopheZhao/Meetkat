import unittest
from typing import Any

from decision_room.runtime.room_event_journal import RoomEventJournal
from decision_room.runtime.room_projector import RoomProjector
from decision_room.tools import (
    MCPToolAdapter,
    Tool,
    ToolDescriptor,
    ToolRegistry,
    ToolResult,
    default_tool_registry,
    register_mcp_tools,
    reset_default_tool_registry_for_tests,
)


class _EchoTool:
    """Minimal in-process Tool used as a registry fixture."""

    name = "echo"
    description = "Returns whatever was passed in"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
    }

    def run(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(ok=True, result=args.get("value"))


class _BrokenTool:
    name = "boom"
    description = "Always raises"
    input_schema: dict[str, Any] = {}

    def run(self, args: dict[str, Any]) -> ToolResult:
        raise RuntimeError("kaboom")


class _NonToolResultTool:
    name = "bad_return"
    description = "Returns the wrong type"
    input_schema: dict[str, Any] = {}

    def run(self, args: dict[str, Any]) -> ToolResult:  # type: ignore[override]
        return {"not": "a ToolResult"}  # type: ignore[return-value]


class _StubMCPClient:
    """In-process MCP client stub used to exercise MCPToolAdapter shape.

    Phase 1 has no real MCP transport — this stub satisfies the protocol
    surface (``list_tools`` / ``call_tool``) so adapter behavior can be
    tested without a server.
    """

    def __init__(
        self,
        tools: list[dict[str, Any]] | None = None,
        responses: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._tools = list(tools or [])
        self._responses = dict(responses or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, args))
        if name in self._responses:
            return self._responses[name]
        return {"ok": True, "result": {"echoed": args}}


class ToolProtocolTests(unittest.TestCase):
    def test_echo_tool_satisfies_protocol(self) -> None:
        tool = _EchoTool()
        self.assertIsInstance(tool, Tool)
        self.assertEqual(tool.name, "echo")
        self.assertTrue(tool.description)
        self.assertEqual(tool.input_schema["type"], "object")

    def test_tool_descriptor_is_serializable(self) -> None:
        descriptor = ToolDescriptor(
            name="echo",
            description="echo back",
            input_schema={"type": "object"},
        )
        payload = descriptor.to_payload()
        self.assertEqual(payload["name"], "echo")
        self.assertEqual(payload["input_schema"]["type"], "object")


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ToolRegistry()

    def test_register_get_list(self) -> None:
        tool = _EchoTool()
        self.registry.register(tool)
        self.assertIs(self.registry.get("echo"), tool)
        self.assertEqual([item.name for item in self.registry.list()], ["echo"])

    def test_register_rejects_blank_name(self) -> None:
        class _Anon:
            name = "  "
            description = ""
            input_schema: dict[str, Any] = {}

            def run(self, args: dict[str, Any]) -> ToolResult:
                return ToolResult(ok=True)

        with self.assertRaises(ValueError):
            self.registry.register(_Anon())

    def test_register_rejects_duplicate(self) -> None:
        self.registry.register(_EchoTool())
        with self.assertRaises(ValueError):
            self.registry.register(_EchoTool())

    def test_unregister_removes_tool(self) -> None:
        self.registry.register(_EchoTool())
        self.registry.unregister("echo")
        self.assertIsNone(self.registry.get("echo"))

    def test_describe_returns_serializable_view(self) -> None:
        self.registry.register(_EchoTool())
        descriptors = self.registry.describe()
        self.assertEqual(len(descriptors), 1)
        self.assertEqual(descriptors[0].to_payload()["name"], "echo")

    def test_dispatch_returns_result_for_registered_tool(self) -> None:
        self.registry.register(_EchoTool())
        result = self.registry.dispatch("echo", {"value": "hi"})
        self.assertTrue(result.ok)
        self.assertEqual(result.result, "hi")
        self.assertGreaterEqual(result.latency_ms, 0)

    def test_dispatch_unknown_tool_returns_error_result(self) -> None:
        result = self.registry.dispatch("missing", {})
        self.assertFalse(result.ok)
        self.assertIn("unknown tool", result.error)

    def test_dispatch_wraps_tool_exception(self) -> None:
        self.registry.register(_BrokenTool())
        result = self.registry.dispatch("boom", {})
        self.assertFalse(result.ok)
        self.assertIn("kaboom", result.error)
        self.assertGreaterEqual(result.latency_ms, 0)

    def test_dispatch_rejects_non_tool_result_return(self) -> None:
        self.registry.register(_NonToolResultTool())
        result = self.registry.dispatch("bad_return", {})
        self.assertFalse(result.ok)
        self.assertIn("expected ToolResult", result.error)


class DefaultRegistrySingletonTests(unittest.TestCase):
    def test_default_registry_is_process_singleton(self) -> None:
        reset_default_tool_registry_for_tests()
        try:
            first = default_tool_registry()
            second = default_tool_registry()
            self.assertIs(first, second)
        finally:
            reset_default_tool_registry_for_tests()

    def test_reset_for_tests_drops_singleton(self) -> None:
        reset_default_tool_registry_for_tests()
        try:
            first = default_tool_registry()
            reset_default_tool_registry_for_tests()
            second = default_tool_registry()
            self.assertIsNot(first, second)
        finally:
            reset_default_tool_registry_for_tests()


class MCPAdapterTests(unittest.TestCase):
    def test_register_mcp_tools_bulk_registers_descriptors(self) -> None:
        client = _StubMCPClient(
            tools=[
                {
                    "name": "search",
                    "description": "Search the web",
                    "input_schema": {"type": "object"},
                },
                {
                    "name": "fetch",
                    "description": "Fetch a URL",
                    "input_schema": {"type": "object"},
                },
            ]
        )
        registry = ToolRegistry()
        names = register_mcp_tools(client, registry)
        self.assertEqual(sorted(names), ["fetch", "search"])
        self.assertEqual(
            sorted(item.name for item in registry.list()),
            ["fetch", "search"],
        )

    def test_register_mcp_tools_supports_prefix(self) -> None:
        client = _StubMCPClient(tools=[{"name": "search", "description": ""}])
        registry = ToolRegistry()
        names = register_mcp_tools(client, registry, name_prefix="mcp.")
        self.assertEqual(names, ["mcp.search"])
        self.assertIsNotNone(registry.get("mcp.search"))

    def test_register_mcp_tools_skips_invalid_descriptors(self) -> None:
        client = _StubMCPClient(
            tools=[
                {"name": "", "description": "no name"},
                "not a dict",  # type: ignore[list-item]
                {"name": "ok", "description": "real one"},
            ]
        )
        registry = ToolRegistry()
        names = register_mcp_tools(client, registry)
        self.assertEqual(names, ["ok"])

    def test_register_mcp_tools_is_idempotent_across_calls(self) -> None:
        client = _StubMCPClient(tools=[{"name": "search", "description": ""}])
        registry = ToolRegistry()
        first = register_mcp_tools(client, registry)
        second = register_mcp_tools(client, registry)
        self.assertEqual(first, ["search"])
        self.assertEqual(second, [])

    def test_adapter_run_marshals_response_into_tool_result(self) -> None:
        client = _StubMCPClient(
            tools=[{"name": "search", "description": ""}],
            responses={"search": {"ok": True, "result": {"docs": []}}},
        )
        adapter = MCPToolAdapter(
            _name="search",
            _description="",
            _input_schema={},
            _client=client,
        )
        result = adapter.run({"query": "decision room"})
        self.assertTrue(result.ok)
        self.assertEqual(result.result, {"docs": []})
        self.assertEqual(client.calls, [("search", {"query": "decision room"})])

    def test_adapter_run_marks_iserror_response_as_failed(self) -> None:
        client = _StubMCPClient(
            tools=[],
            responses={
                "search": {
                    "ok": True,
                    "isError": True,
                    "error": "upstream 500",
                }
            },
        )
        adapter = MCPToolAdapter(
            _name="search",
            _description="",
            _input_schema={},
            _client=client,
        )
        result = adapter.run({})
        self.assertFalse(result.ok)
        self.assertIn("upstream 500", result.error)

    def test_adapter_run_surfaces_transport_exception_as_tool_result(self) -> None:
        class _BrokenClient:
            def list_tools(self) -> list[dict[str, Any]]:
                return []

            def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
                raise ConnectionError("transport down")

        adapter = MCPToolAdapter(
            _name="search",
            _description="",
            _input_schema={},
            _client=_BrokenClient(),
        )
        result = adapter.run({})
        self.assertFalse(result.ok)
        self.assertIn("transport down", result.error)


class ProjectorToolEventTests(unittest.TestCase):
    def test_tool_call_projected_as_pending_then_tool_result_settles(self) -> None:
        journal = RoomEventJournal("room_tool")
        projector = RoomProjector("room_tool")
        call_event = journal.append(
            producer_id="agent.implementation_specialist.1",
            role="implementation_specialist",
            event_type="agent.tool_call",
            payload={
                "call_id": "call_1",
                "tool_name": "search",
                "agent_role": "implementation_specialist",
                "args": {"query": "decision room"},
            },
        )
        projector.apply(call_event)
        self.assertEqual(len(projector.snapshot.tool_calls), 1)
        record = projector.snapshot.tool_calls[0]
        self.assertEqual(record.call_id, "call_1")
        self.assertEqual(record.tool_name, "search")
        self.assertEqual(record.agent_role, "implementation_specialist")
        self.assertEqual(record.status, "pending")
        self.assertEqual(record.args, {"query": "decision room"})

        result_event = journal.append(
            producer_id="agent.implementation_specialist.1",
            role="implementation_specialist",
            event_type="agent.tool_result",
            payload={
                "call_id": "call_1",
                "ok": True,
                "result": {"docs": [1, 2, 3]},
                "latency_ms": 42,
            },
        )
        projector.apply(result_event)
        record = projector.snapshot.tool_calls[0]
        self.assertEqual(record.status, "ok")
        self.assertEqual(record.result, {"docs": [1, 2, 3]})
        self.assertEqual(record.latency_ms, 42)

    def test_failing_tool_result_marks_record_as_error(self) -> None:
        journal = RoomEventJournal("room_tool")
        projector = RoomProjector("room_tool")
        projector.apply(
            journal.append(
                producer_id="agent.risk_specialist.1",
                role="risk_specialist",
                event_type="agent.tool_call",
                payload={
                    "call_id": "call_x",
                    "tool_name": "search",
                    "args": {},
                },
            )
        )
        projector.apply(
            journal.append(
                producer_id="agent.risk_specialist.1",
                role="risk_specialist",
                event_type="agent.tool_result",
                payload={
                    "call_id": "call_x",
                    "ok": False,
                    "error": "upstream timeout",
                    "latency_ms": 1500,
                },
            )
        )
        record = projector.snapshot.tool_calls[0]
        self.assertEqual(record.status, "error")
        self.assertEqual(record.error, "upstream timeout")
        self.assertEqual(record.latency_ms, 1500)

    def test_projector_idempotent_on_replay(self) -> None:
        journal = RoomEventJournal("room_tool")
        journal.append(
            producer_id="agent.implementation_specialist.1",
            role="implementation_specialist",
            event_type="agent.tool_call",
            payload={"call_id": "call_idem", "tool_name": "search", "args": {}},
        )
        journal.append(
            producer_id="agent.implementation_specialist.1",
            role="implementation_specialist",
            event_type="agent.tool_result",
            payload={"call_id": "call_idem", "ok": True, "result": "x"},
        )

        primary = journal.rebuild(RoomProjector("room_tool"))
        rebuilt = journal.rebuild(RoomProjector("room_tool"))
        self.assertEqual(len(primary.snapshot.tool_calls), 1)
        self.assertEqual(len(rebuilt.snapshot.tool_calls), 1)
        self.assertEqual(
            primary.snapshot.tool_calls[0].result,
            rebuilt.snapshot.tool_calls[0].result,
        )

    def test_tool_result_without_matching_call_is_noop(self) -> None:
        journal = RoomEventJournal("room_tool")
        projector = RoomProjector("room_tool")
        projector.apply(
            journal.append(
                producer_id="agent.risk_specialist.1",
                role="risk_specialist",
                event_type="agent.tool_result",
                payload={"call_id": "missing", "ok": True, "result": "x"},
            )
        )
        self.assertEqual(projector.snapshot.tool_calls, [])

    def test_tool_call_payload_without_call_id_is_skipped(self) -> None:
        journal = RoomEventJournal("room_tool")
        projector = RoomProjector("room_tool")
        projector.apply(
            journal.append(
                producer_id="agent.risk_specialist.1",
                role="risk_specialist",
                event_type="agent.tool_call",
                payload={"tool_name": "search"},
            )
        )
        self.assertEqual(projector.snapshot.tool_calls, [])


if __name__ == "__main__":
    unittest.main()
