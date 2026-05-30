from __future__ import annotations

import threading
import time
from typing import Any

from .base import Tool, ToolDescriptor, ToolResult


class ToolRegistry:
    """Thread-safe registry of available tools, keyed by ``tool.name``.

    The registry is intentionally narrow: it knows how to register, look up,
    list, and dispatch. It does NOT know about agents, rooms, or events —
    callers (agents) wrap the dispatch with ``agent.tool_call`` /
    ``agent.tool_result`` journal events. Keeping dispatch journal-agnostic
    lets the same registry be reused from tests, governance harnesses, and
    in-process tooling.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._lock = threading.Lock()

    def register(self, tool: Tool) -> None:
        name = tool.name.strip()
        if not name:
            raise ValueError("tool.name must be a non-empty string")
        with self._lock:
            if name in self._tools:
                raise ValueError(f"tool already registered: {name}")
            self._tools[name] = tool

    def unregister(self, name: str) -> None:
        with self._lock:
            self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        with self._lock:
            return self._tools.get(name)

    def list(self) -> list[Tool]:
        with self._lock:
            return list(self._tools.values())

    def describe(self) -> list[ToolDescriptor]:
        with self._lock:
            return [
                ToolDescriptor(
                    name=tool.name,
                    description=tool.description,
                    input_schema=dict(tool.input_schema),
                )
                for tool in self._tools.values()
            ]

    def dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        """Invoke a registered tool by name and return its result.

        Wraps the underlying ``tool.run`` so callers get a uniform
        ``ToolResult`` even when the tool raises, and so latency is measured
        consistently for every tool regardless of implementation.
        """
        tool = self.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"unknown tool: {name}", latency_ms=0)
        started_ns = time.perf_counter_ns()
        try:
            result = tool.run(dict(args or {}))
        except Exception as exc:  # noqa: BLE001 — tool failures must not bubble
            elapsed_ms = (time.perf_counter_ns() - started_ns) // 1_000_000
            return ToolResult(ok=False, error=str(exc), latency_ms=int(elapsed_ms))
        elapsed_ms = (time.perf_counter_ns() - started_ns) // 1_000_000
        if not isinstance(result, ToolResult):
            return ToolResult(
                ok=False,
                error=(
                    f"tool {name!r} returned {type(result).__name__}, "
                    "expected ToolResult"
                ),
                latency_ms=int(elapsed_ms),
            )
        if result.latency_ms:
            return result
        return ToolResult(
            ok=result.ok,
            result=result.result,
            error=result.error,
            latency_ms=int(elapsed_ms),
        )


_default_registry: ToolRegistry | None = None
_default_lock = threading.Lock()


def default_tool_registry() -> ToolRegistry:
    """Process-wide singleton, matching ``default_room_memory_store`` style.

    Phase 1 keeps this empty by default — Phase 2 (agent loop) is where
    agents start consulting it. Governance/test harnesses can register
    fixture tools here without each room building its own registry.
    """
    global _default_registry
    with _default_lock:
        if _default_registry is None:
            _default_registry = ToolRegistry()
        return _default_registry


def reset_default_tool_registry_for_tests() -> None:
    """Drop the cached singleton so tests can rebind registrations."""
    global _default_registry
    with _default_lock:
        _default_registry = None
