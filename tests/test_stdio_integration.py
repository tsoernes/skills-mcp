"""
test_stdio_integration.py

Integration tests for the skills-mcp MCP server over stdio transport.

These tests spawn the actual server process and communicate with it using
the MCP protocol over stdin/stdout, validating the full stack including:
- Server startup and initialization
- Tool discovery and schema validation
- Tool invocation with various parameters
- Error handling and edge cases
- Server shutdown and cleanup

Requirements:
- skills-mcp server must be installed in the environment
- skills directory must exist with valid SKILL.md files
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest


class MCPStdioClient:
    """
    Minimal MCP client for stdio integration testing.

    Implements the MCP protocol over stdin/stdout to communicate with
    a spawned server process. Handles JSON-RPC message framing and
    request/response correlation.
    """

    def __init__(self, command: list[str], cwd: Path | None = None):
        """
        Initialize client but don't start the server yet.

        Args:
            command: Command and arguments to spawn the server
            cwd: Working directory for the server process
        """
        self.command = command
        self.cwd = cwd
        self.process: subprocess.Popen | None = None
        self.request_id = 0

    def start(self) -> None:
        """Start the server process."""
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            text=True,
            bufsize=1,
        )
        # Give server a moment to initialize
        time.sleep(0.5)

    def stop(self) -> None:
        """Stop the server process gracefully."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            finally:
                self.process = None

    def _next_request_id(self) -> int:
        """Generate next request ID."""
        self.request_id += 1
        return self.request_id

    def _send_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Send a JSON-RPC request and wait for response.

        Args:
            method: JSON-RPC method name
            params: Optional parameters dictionary

        Returns:
            Response result dictionary

        Raises:
            RuntimeError: If server is not running or communication fails
            ValueError: If server returns an error response or isError flag is set
        """
        if not self.process or not self.process.stdin or not self.process.stdout:
            raise RuntimeError("Server not running")

        request_id = self._next_request_id()
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }

        # Only include params if provided and non-empty
        if params:
            request["params"] = params

        # Send request
        request_line = json.dumps(request) + "\n"
        self.process.stdin.write(request_line)
        self.process.stdin.flush()

        # Read response
        response_line = self.process.stdout.readline()
        if not response_line:
            stderr_output = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"No response from server. stderr: {stderr_output}")

        response = json.loads(response_line)

        # Check for JSON-RPC error
        if "error" in response:
            error = response["error"]
            raise ValueError(f"Server error: {error.get('message', error)}")

        result = response.get("result", {})

        # Check for tool execution error (isError flag)
        if isinstance(result, dict) and result.get("isError"):
            error_msg = "Tool execution error"
            if "content" in result and result["content"]:
                # Extract error message from content
                content = result["content"][0]
                if content.get("type") == "text":
                    error_msg = content.get("text", error_msg)
            raise ValueError(error_msg)

        return result

    def initialize(self) -> dict[str, Any]:
        """
        Send MCP initialize request and initialized notification.

        Returns:
            Server capabilities and metadata
        """
        result = self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "test-client",
                    "version": "1.0.0",
                },
            },
        )

        # Send initialized notification (no response expected)
        if self.process and self.process.stdin:
            notification = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
            notification_line = json.dumps(notification) + "\n"
            self.process.stdin.write(notification_line)
            self.process.stdin.flush()
            # Small delay to let server process the notification
            time.sleep(0.1)

        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """
        List available tools from the server.

        Returns:
            List of tool definitions with name, description, and input schema
        """
        result = self._send_request("tools/list")
        return result.get("tools", [])

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """
        Call a tool on the server.

        Args:
            name: Tool name
            arguments: Tool arguments dictionary

        Returns:
            List of content items from tool execution
        """
        result = self._send_request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )
        return result.get("content", [])


@pytest.fixture
def repo_root() -> Path:
    """Get the repository root directory."""
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def mcp_client(repo_root: Path) -> MCPStdioClient:
    """
    Create and start an MCP stdio client connected to the server.

    Yields the client and ensures cleanup on teardown.
    """
    # Use the installed package entry point
    client = MCPStdioClient(
        command=[sys.executable, "-m", "skills_mcp.server"],
        cwd=repo_root,
    )

    try:
        client.start()
        yield client
    finally:
        client.stop()


def test_server_starts_and_initializes(mcp_client: MCPStdioClient) -> None:
    """Test that server starts and responds to initialize."""
    result = mcp_client.initialize()

    assert "protocolVersion" in result
    assert "capabilities" in result
    assert "serverInfo" in result

    server_info = result["serverInfo"]
    assert server_info["name"] == "ClaudeSkills"
    assert "version" in server_info


def test_list_tools_returns_expected_tools(mcp_client: MCPStdioClient) -> None:
    """Test that server exposes expected MCP tools."""
    mcp_client.initialize()
    tools = mcp_client.list_tools()

    assert isinstance(tools, list)
    assert len(tools) > 0

    # Extract tool names
    tool_names = {tool["name"] for tool in tools}

    # Expected core tools based on server.py
    expected_tools = {
        "skill_server_info",
        "skill_list_all",
        "skill_get_detail",
        "skill_search_index",
        "skill_list_assets",
        "skill_read_asset",
        "skill_create",
        "skill_add_asset",
        "skill_add_assets",
        "skill_store_note",
        "skill_list_notes",
        "skill_trash_user_skill",
        "skill_trash_user_asset",
    }

    assert expected_tools.issubset(tool_names), (
        f"Missing tools: {expected_tools - tool_names}"
    )


def test_tool_schemas_are_valid(mcp_client: MCPStdioClient) -> None:
    """Test that all tools have valid JSON schemas."""
    mcp_client.initialize()
    tools = mcp_client.list_tools()

    for tool in tools:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool

        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert "properties" in schema


def test_skill_server_info(mcp_client: MCPStdioClient) -> None:
    """Test skill_server_info tool."""
    mcp_client.initialize()
    result = mcp_client.call_tool("skill_server_info")

    assert len(result) > 0
    content = result[0]
    assert content["type"] == "text"

    # Try structuredContent first, fall back to parsing text
    data = content.get("structuredContent")
    if not data:
        data = json.loads(content["text"])
    assert "name" in data
    assert data["name"] == "ClaudeSkills"
    assert "skills_dir" in data
    assert "transport" in data


def test_skill_list_all(mcp_client: MCPStdioClient) -> None:
    """Test skill_list_all tool returns valid skill list."""
    mcp_client.initialize()
    result = mcp_client.call_tool("skill_list_all")

    assert len(result) > 0
    content = result[0]
    assert content["type"] == "text"

    # Parse the JSON response
    skills = json.loads(content["text"])
    assert isinstance(skills, list)

    if len(skills) > 0:
        skill = skills[0]
        assert "name" in skill
        assert "description" in skill
        assert "path" in skill
        assert isinstance(skill["name"], str)
        assert isinstance(skill["description"], str)


def test_skill_list_all_markdown(mcp_client: MCPStdioClient) -> None:
    """Test skill_list_all with markdown output."""
    mcp_client.initialize()
    result = mcp_client.call_tool("skill_list_all", {"markdown_output": True})

    assert len(result) > 0
    content = result[0]
    assert content["type"] == "text"

    # Should return markdown format
    text = content["text"]
    assert isinstance(text, str)
    # Markdown output typically has headers and formatting
    assert len(text) > 0


def test_skill_get_detail(mcp_client: MCPStdioClient, repo_root: Path) -> None:
    """Test skill_get_detail tool for a specific skill."""
    mcp_client.initialize()

    # First get list of skills to find a valid name
    list_result = mcp_client.call_tool("skill_list_all")
    skills = json.loads(list_result[0]["text"])

    if not skills:
        pytest.skip("No skills available to test")

    skill_name = skills[0]["name"]

    # Get detail for the first skill
    result = mcp_client.call_tool("skill_get_detail", {"name": skill_name})

    assert len(result) > 0
    content = result[0]
    assert content["type"] == "text"

    detail = json.loads(content["text"])
    assert detail["name"] == skill_name
    assert "description" in detail
    assert "body" in detail
    assert isinstance(detail["body"], str)


def test_skill_get_detail_with_notes(mcp_client: MCPStdioClient) -> None:
    """Test skill_get_detail with include_notes parameter."""
    mcp_client.initialize()

    # Get first skill
    list_result = mcp_client.call_tool("skill_list_all")
    skills = json.loads(list_result[0]["text"])

    if not skills:
        pytest.skip("No skills available to test")

    skill_name = skills[0]["name"]

    # Get detail with notes explicitly included
    result = mcp_client.call_tool(
        "skill_get_detail", {"name": skill_name, "include_notes": True}
    )

    assert len(result) > 0
    content = result[0]
    detail = json.loads(content["text"])
    assert "body" in detail


def test_skill_search_index(mcp_client: MCPStdioClient) -> None:
    """Test skill_search_index tool."""
    mcp_client.initialize()

    # Search for common term that likely exists
    result = mcp_client.call_tool("skill_search_index", {"query": "skill"})

    assert len(result) > 0
    content = result[0]
    assert content["type"] == "text"

    matches = json.loads(content["text"])
    assert isinstance(matches, list)


def test_skill_list_assets(mcp_client: MCPStdioClient) -> None:
    """Test skill_list_assets tool."""
    mcp_client.initialize()

    # Get first skill
    list_result = mcp_client.call_tool("skill_list_all")
    skills = json.loads(list_result[0]["text"])

    if not skills:
        pytest.skip("No skills available to test")

    skill_name = skills[0]["name"]

    # List assets (may be empty)
    result = mcp_client.call_tool("skill_list_assets", {"name": skill_name})

    assert len(result) > 0
    content = result[0]
    assert content["type"] == "text"

    assets = json.loads(content["text"])
    assert isinstance(assets, list)


def test_skill_list_notes(mcp_client: MCPStdioClient) -> None:
    """Test skill_list_notes tool."""
    mcp_client.initialize()

    # Get first skill
    list_result = mcp_client.call_tool("skill_list_all")
    skills = json.loads(list_result[0]["text"])

    if not skills:
        pytest.skip("No skills available to test")

    skill_name = skills[0]["name"]

    # List notes (may be empty)
    result = mcp_client.call_tool("skill_list_notes", {"name": skill_name})

    # Result content may be empty if no notes exist
    if len(result) > 0:
        content = result[0]
        assert content["type"] == "text"

        notes = json.loads(content["text"])
        assert isinstance(notes, list)
    else:
        # Empty result is acceptable when skill has no notes
        pass


def test_invalid_tool_name_raises_error(mcp_client: MCPStdioClient) -> None:
    """Test that calling non-existent tool raises error."""
    mcp_client.initialize()

    with pytest.raises(ValueError):
        mcp_client.call_tool("nonexistent_tool")


def test_missing_required_parameter_raises_error(mcp_client: MCPStdioClient) -> None:
    """Test that missing required parameters raises error."""
    mcp_client.initialize()

    # skill_get_detail requires 'name' parameter
    with pytest.raises(ValueError):
        mcp_client.call_tool("skill_get_detail", {})


def test_invalid_skill_name_returns_error(mcp_client: MCPStdioClient) -> None:
    """Test that invalid skill name is handled gracefully."""
    mcp_client.initialize()

    # Should get error for non-existent skill
    with pytest.raises(ValueError):
        mcp_client.call_tool(
            "skill_get_detail", {"name": "nonexistent-skill-name-12345"}
        )


def test_skill_read_asset_path_traversal_blocked(mcp_client: MCPStdioClient) -> None:
    """Test that path traversal attempts are blocked in skill_read_asset."""
    mcp_client.initialize()

    # Get first skill
    list_result = mcp_client.call_tool("skill_list_all")
    skills = json.loads(list_result[0]["text"])

    if not skills:
        pytest.skip("No skills available to test")

    skill_name = skills[0]["name"]

    # Attempt path traversal
    with pytest.raises(ValueError):
        mcp_client.call_tool(
            "skill_read_asset", {"name": skill_name, "path": "../../../etc/passwd"}
        )


def test_concurrent_requests(mcp_client: MCPStdioClient) -> None:
    """Test that multiple sequential requests work correctly."""
    mcp_client.initialize()

    # Make multiple requests in sequence
    for _ in range(3):
        result = mcp_client.call_tool("skill_server_info")
        assert len(result) > 0
        # Try structuredContent first, fall back to parsing text
        data = result[0].get("structuredContent")
        if not data:
            data = json.loads(result[0]["text"])
        assert data["name"] == "ClaudeSkills"


def test_server_handles_large_response(mcp_client: MCPStdioClient) -> None:
    """Test that server can handle and return large responses."""
    mcp_client.initialize()

    # Get all skills which might be a large response
    result = mcp_client.call_tool("skill_list_all")

    assert len(result) > 0
    content = result[0]
    assert content["type"] == "text"

    # Parse to ensure it's valid JSON
    skills = json.loads(content["text"])
    assert isinstance(skills, list)


def test_markdown_output_parameter(mcp_client: MCPStdioClient) -> None:
    """Test markdown_output parameter across different tools."""
    mcp_client.initialize()

    # Get first skill
    list_result = mcp_client.call_tool("skill_list_all")
    skills = json.loads(list_result[0]["text"])

    if not skills:
        pytest.skip("No skills available to test")

    skill_name = skills[0]["name"]

    # Test markdown output for get_detail
    result_md = mcp_client.call_tool(
        "skill_get_detail", {"name": skill_name, "markdown_output": True}
    )
    assert len(result_md) > 0
    assert result_md[0]["type"] == "text"
    assert isinstance(result_md[0]["text"], str)

    # Test JSON output (default)
    result_json = mcp_client.call_tool(
        "skill_get_detail", {"name": skill_name, "markdown_output": False}
    )
    assert len(result_json) > 0
    # Should be valid JSON
    json.loads(result_json[0]["text"])


if __name__ == "__main__":
    # Allow running this file directly for debugging
    pytest.main([__file__, "-v"])
