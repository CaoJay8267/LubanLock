"""Import helpers: Claude Code projects and DotAgent packages."""

from __future__ import annotations

from .claude import ImportReport, import_claude, import_package
from .copilot import import_copilot, looks_like_copilot
from .dsh import DshImportReport, import_dsh, looks_like_dsh
from .mcp_client import (
    McpDiscovery,
    McpImportReport,
    discover_agent,
    discover_server,
    import_tools,
)
from .openai import OpenAIImportReport, import_openai, looks_like_openai

__all__ = [
    "DshImportReport",
    "ImportReport",
    "McpDiscovery",
    "McpImportReport",
    "OpenAIImportReport",
    "discover_agent",
    "discover_server",
    "import_claude",
    "import_copilot",
    "import_dsh",
    "import_openai",
    "import_package",
    "import_tools",
    "looks_like_copilot",
    "looks_like_dsh",
    "looks_like_openai",
]
