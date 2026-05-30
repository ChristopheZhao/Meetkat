"""MCP tool adapter — exposes an MCP-served tool through the local
``Tool`` protocol so agents see one uniform registry.

Phase 1 ships the adapter SHAPE only — there is no MCP server dependency
yet. ``MCPClient`` is a small Protocol that any future MCP transport
(stdio, HTTP, websocket) must satisfy. ``MCPToolAdapter`` wraps one
descriptor + client pair into a single ``Tool``. ``register_mcp_tools``
takes a client and bulk-registers every tool the client exposes, so
later phases can register an entire MCP server with one call.

PRD §7.4 says MCP is the tool/resource access boundary, not a parallel
runtime. This adapter is what enforces that — MCP tools are just
``Tool`` instances in the same ``ToolRegistry``, not a second framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable

from .base import Tool, ToolResult
from .registry import ToolRegistry


@runtime_checkable
class MCPClient(Protocol):
    """Minimal MCP client surface used by the adapter.

    A concrete client (stdio/HTTP/websocket) implements ``list_tools`` for
    discovery and ``call_tool`` for dispatch. The adapter does NOT manage
    the client lifecycle — connect/disconnect belongs to the caller that
    registers the tools.
    """

    def list_tools(self) -> list[dict[str, Any]]:
        ...

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class MCPToolAdapter:
    """One MCP-served tool exposed via the local ``Tool`` protocol."""

    _name: str
    _description: str
    _input_schema: dict[str, Any]
    _client: MCPClient

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> dict[str, Any]:
        return dict(self._input_schema)

    def run(self, args: dict[str, Any]) -> ToolResult:
        try:
            response = self._client.call_tool(self._name, args)
        except Exception as exc:  # noqa: BLE001 — surface transport failures uniformly
            return ToolResult(ok=False, error=f"mcp call failed: {exc}")
        if not isinstance(response, dict):
            return ToolResult(
                ok=False,
                error=f"mcp tool {self._name!r} returned non-dict response",
            )
        ok = bool(response.get("ok", True)) and not response.get("isError", False)
        result = response.get("result", response.get("content"))
        error = str(response.get("error", "")).strip()
        return ToolResult(ok=ok, result=result, error=error)


def register_mcp_tools(
    client: MCPClient,
    registry: ToolRegistry,
    *,
    name_prefix: str = "",
) -> list[str]:
    """Discover tools from an MCP client and register them all.

    Returns the names that were registered (after prefixing). Tools that
    fail the descriptor shape check are skipped silently — discovery is
    best-effort because an MCP server may legitimately advertise tools the
    local code cannot model yet.
    """
    registered: list[str] = []
    descriptors = _safe_list_tools(client)
    for descriptor in descriptors:
        name = str(descriptor.get("name", "")).strip()
        if not name:
            continue
        prefixed = f"{name_prefix}{name}" if name_prefix else name
        adapter = MCPToolAdapter(
            _name=prefixed,
            _description=str(descriptor.get("description", "")).strip(),
            _input_schema=_coerce_schema(descriptor.get("input_schema")),
            _client=client,
        )
        try:
            registry.register(adapter)
        except ValueError:
            # Already registered — skip without overwriting.
            continue
        registered.append(prefixed)
    return registered


def _safe_list_tools(client: MCPClient) -> Iterable[dict[str, Any]]:
    try:
        listing = client.list_tools()
    except Exception:  # noqa: BLE001 — discovery must never break registry
        return []
    if not isinstance(listing, list):
        return []
    return [item for item in listing if isinstance(item, dict)]


def _coerce_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}
