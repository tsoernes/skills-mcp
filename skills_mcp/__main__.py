"""
Package entry point for launching the skills_mcp server module.

This allows running:
  - python -m skills_mcp            -> invokes skills_mcp.server CLI
  - python -m skills_mcp.server     -> also available directly via the server module

The entry point delegates to skills_mcp.server.cli_main() which supports both
CLI inspection modes and starting the stdio MCP server.
"""

from skills_mcp.server import cli_main


def main() -> None:
    cli_main()


if __name__ == "__main__":
    main()
