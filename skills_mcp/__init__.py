"""
skills_mcp: FastMCP stdio server package exposing Anthropic Claude Agent Skills as MCP tools.

This package provides the server entrypoint and utilities to discover, parse, and serve
Agent Skills Spec-compliant skill folders to MCP-aware clients.
"""

__version__: str = "0.1.0"


def version() -> str:
    return __version__


__all__: list[str] = ["__version__", "version"]
