"""Read-only guard for the Seapoint MCP.

CashCycle never changes anything in Seapoint. Every tool name the app (or the
agent driving it) wants to call goes through `assert_read_only` first. Only
`query_*` tools and `get_current_user` pass; `set_*`, `update_*`,
`approve_*` and anything unknown are refused before a request is built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

SERVER_PREFIX = re.compile(r"^mcp__[A-Za-z0-9_]+?__")
READ_ONLY_TOOLS = frozenset({"get_current_user"})
READ_ONLY_PREFIXES = ("query_",)
WRITE_PREFIXES = ("set_", "update_", "approve_", "create_", "delete_", "cancel_", "send_", "pay_", "post_")


class WriteToolRefused(PermissionError):
    """Raised before any write-capable Seapoint tool is called."""


def bare_name(tool_name: str) -> str:
    return SERVER_PREFIX.sub("", tool_name.strip())


def is_read_only(tool_name: str) -> bool:
    name = bare_name(tool_name)
    return name in READ_ONLY_TOOLS or name.startswith(READ_ONLY_PREFIXES)


def assert_read_only(tool_name: str) -> str:
    """Return the bare tool name if it is read-only; raise WriteToolRefused otherwise."""
    name = bare_name(tool_name)
    if is_read_only(name):
        return name
    kind = "write" if name.startswith(WRITE_PREFIXES) else "unknown"
    raise WriteToolRefused(f"refused {kind} tool '{tool_name}': CashCycle is read-only on Seapoint (allowed: query_* and get_current_user)")


@dataclass
class GuardedSeapoint:
    """Wrap any callable that invokes an MCP tool by name so writes can never reach it.

    `transport(tool_name, arguments)` is whatever actually talks to the MCP.
    Every call is recorded in `calls` so the audit log can list exactly what was read.
    """

    transport: Callable[[str, dict[str, Any]], Any]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        arguments = dict(arguments or {})
        try:
            name = assert_read_only(tool_name)
        except WriteToolRefused:
            self.refused.append(tool_name)
            raise
        self.calls.append((name, arguments))
        return self.transport(name, arguments)
