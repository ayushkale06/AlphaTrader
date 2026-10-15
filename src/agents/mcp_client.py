"""Unified MCP Client interface for agents supporting both mock/in-process and live sessions.

Governance: ADR-011.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("alphatrader.agents.mcp_client")


class MCPClientError(Exception):
    """Raised when an MCP client session encounters transport or protocol failures."""


class MCPClientSession(ABC):
    """Abstract interface for calling FastMCP tools from autonomous agents."""

    @abstractmethod
    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Invoke an MCP tool with timeout and return structured dictionary."""


class InMemoryMCPClientSession(MCPClientSession):
    """Deterministic in-memory/direct adapter for unit tests and local composition."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], Any] = {}

    def register_tool(self, server_name: str, tool_name: str, handler: Any) -> None:
        """Register a callable handler for a server and tool name."""
        self._handlers[(server_name, tool_name)] = handler

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Directly dispatch registered handler."""
        key = (server_name, tool_name)
        if key not in self._handlers:
            return {
                "error": "TOOL_NOT_FOUND",
                "message": f"Server '{server_name}' has no registered tool '{tool_name}'.",
            }
        try:
            handler = self._handlers[key]
            res = handler(**arguments)
            if isinstance(res, str):
                import json

                try:
                    parsed = json.loads(res)
                    if isinstance(parsed, dict):
                        return parsed
                    return {"result": parsed}
                except Exception:
                    return {"result": res}
            if isinstance(res, dict):
                return res
            return {"result": res}
        except Exception as e:
            logger.exception("Error executing in-memory tool %s.%s: %s", server_name, tool_name, e)
            return {"error": "TOOL_EXECUTION_ERROR", "message": str(e)}
