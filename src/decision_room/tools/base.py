from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolResult:
    """Outcome of one tool invocation.

    ``ok=False`` carries ``error`` and an empty ``result``. Latency is measured
    by ``ToolRegistry.dispatch`` so individual ``Tool`` implementations don't
    have to time themselves.
    """

    ok: bool
    result: Any = None
    error: str = ""
    latency_ms: int = 0


@runtime_checkable
class Tool(Protocol):
    """Pluggable capability invoked from inside an agent's iteration loop.

    Tools are NOT free-floating runtime hooks: they run only when an agent
    decides to call them. The registry dispatches by ``name``; the agent
    emits ``agent.tool_call`` before invocation and ``agent.tool_result`` after.
    """

    @property
    def name(self) -> str:
        ...

    @property
    def description(self) -> str:
        ...

    @property
    def input_schema(self) -> dict[str, Any]:
        ...

    def run(self, args: dict[str, Any]) -> ToolResult:
        ...


@dataclass(frozen=True)
class ToolDescriptor:
    """Serializable view of a tool used by prompt builders and the UI.

    ``ToolRegistry.describe()`` returns a list of these so an agent's prompt
    can advertise the registered tool catalog without importing the
    implementations.
    """

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }
