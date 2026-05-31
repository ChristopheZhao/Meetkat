"""Pluggable tool surface for decision-room agents.

Phase 1 of the native-agent refactor: introduces the extension seam so
later phases can wire agents through it. The shape mirrors PRD §6.4
(``tool_call`` is a first-class agent capability) and §7.4 (MCP is the
tool/resource boundary, not a parallel runtime) — MCP tools are just
``Tool`` instances in the same ``ToolRegistry``.

Surface:

- ``Tool``               — protocol every tool must satisfy
- ``ToolResult``         — uniform return shape (ok / result / error / latency_ms)
- ``ToolDescriptor``     — serializable view used by prompts and the UI
- ``ToolRegistry``       — register / get / list / describe / dispatch
- ``MCPToolAdapter``     — wraps an MCP-served tool through the local ``Tool`` protocol
- ``register_mcp_tools`` — bulk-register every tool advertised by one MCP client
"""

from .base import Tool, ToolDescriptor, ToolResult
from .mcp_adapter import MCPClient, MCPToolAdapter, register_mcp_tools
from .registry import (
    ToolRegistry,
    default_tool_registry,
    reset_default_tool_registry_for_tests,
)

__all__ = [
    "MCPClient",
    "MCPToolAdapter",
    "Tool",
    "ToolDescriptor",
    "ToolRegistry",
    "ToolResult",
    "default_tool_registry",
    "register_mcp_tools",
    "reset_default_tool_registry_for_tests",
]
