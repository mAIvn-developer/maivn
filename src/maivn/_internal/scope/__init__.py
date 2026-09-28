"""Agent and Swarm authoring facade."""

from __future__ import annotations

from maivn._internal.scope.agent import Agent
from maivn._internal.scope.followup import FollowupInvocationBuilder
from maivn._internal.scope.swarm import Swarm
from maivn._internal.scope.tools import compile_mcp_tools

# Back-compat seam: tests reach the MCP tool compiler through this module by its
# pre-split private name. The implementation now lives in scope.tools.
_compile_mcp_tools = compile_mcp_tools

__all__ = ['Agent', 'FollowupInvocationBuilder', 'Swarm']
