#!/usr/bin/env python3
"""
stdio_test_client.py

Standalone stdio test client for the skills-mcp MCP server.

This script can be run directly to manually test the server over stdio transport.
It provides an interactive or scripted way to exercise the MCP protocol and
validate server behavior.

Usage:
    # Run all automated tests
    python stdio_test_client.py

    # Run specific test
    python stdio_test_client.py --test list_tools

    # Interactive mode
    python stdio_test_client.py --interactive

    # Verbose output
    python stdio_test_client.py --verbose
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


class Colors:
    """ANSI color codes for terminal output."""

    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


class MCPStdioClient:
    """
    MCP stdio client for testing the skills-mcp server.

    Implements the MCP protocol over stdin/stdout for communication
    with the server process.
    """

    def __init__(
        self,
        command: list[str],
        cwd: Path | None = None,
        verbose: bool = False,
    ):
        """
        Initialize the MCP stdio client.

        Args:
            command: Command and arguments to spawn the server
            cwd: Working directory for the server process
            verbose: Enable verbose logging
        """
        self.command = command
        self.cwd = cwd
        self.verbose = verbose
        self.process: subprocess.Popen | None = None
        self.request_id = 0

    def log(self, message: str, color: str = "") -> None:
        """Log a message if verbose mode is enabled."""
        if self.verbose:
            print(f"{color}{message}{Colors.ENDC}")

    def start(self) -> None:
        """Start the MCP server process."""
        self.log(f"Starting server: {' '.join(self.command)}", Colors.OKCYAN)
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            text=True,
            bufsize=1,
        )
        time.sleep(0.5)
        self.log("Server started successfully", Colors.OKGREEN)

    def stop(self) -> None:
        """Stop the server process gracefully."""
        if self.process:
            self.log("Stopping server...", Colors.OKCYAN)
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
                self.log("Server stopped successfully", Colors.OKGREEN)
            except subprocess.TimeoutExpired:
                self.log("Server did not stop gracefully, killing...", Colors.WARNING)
                self.process.kill()
                self.process.wait()
            finally:
                self.process = None

    def _next_request_id(self) -> int:
        """Generate next request ID."""
        self.request_id += 1
        return self.request_id

    def _send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
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

        self.log(f"→ Request: {json.dumps(request, indent=2)}", Colors.OKBLUE)

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
        self.log(f"← Response: {json.dumps(response, indent=2)}", Colors.OKCYAN)

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
        """Send MCP initialize request and initialized notification."""
        result = self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "stdio-test-client",
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
            self.log(f"→ Notification: {json.dumps(notification)}", Colors.OKBLUE)
            # Small delay to let server process the notification
            time.sleep(0.1)

        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """List available tools from the server."""
        result = self._send_request("tools/list")
        return result.get("tools", [])

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
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


class TestRunner:
    """Test runner for MCP stdio client tests."""

    def __init__(self, client: MCPStdioClient):
        """Initialize test runner with client."""
        self.client = client
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def print_header(self, text: str) -> None:
        """Print a test header."""
        print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{text}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 70}{Colors.ENDC}\n")

    def print_test(self, name: str) -> None:
        """Print test name."""
        print(f"{Colors.BOLD}TEST: {name}{Colors.ENDC}")

    def print_pass(self, message: str = "") -> None:
        """Print test passed."""
        self.passed += 1
        msg = f"  {Colors.OKGREEN}✓ PASSED{Colors.ENDC}"
        if message:
            msg += f" - {message}"
        print(msg)

    def print_fail(self, message: str) -> None:
        """Print test failed."""
        self.failed += 1
        print(f"  {Colors.FAIL}✗ FAILED{Colors.ENDC} - {message}")

    def print_skip(self, message: str) -> None:
        """Print test skipped."""
        self.skipped += 1
        print(f"  {Colors.WARNING}⊘ SKIPPED{Colors.ENDC} - {message}")

    def print_summary(self) -> None:
        """Print test summary."""
        total = self.passed + self.failed + self.skipped
        print(f"\n{Colors.BOLD}{'=' * 70}{Colors.ENDC}")
        print(f"{Colors.BOLD}TEST SUMMARY{Colors.ENDC}")
        print(f"{Colors.BOLD}{'=' * 70}{Colors.ENDC}")
        print(f"  Total:   {total}")
        print(f"  {Colors.OKGREEN}Passed:  {self.passed}{Colors.ENDC}")
        print(f"  {Colors.FAIL}Failed:  {self.failed}{Colors.ENDC}")
        print(f"  {Colors.WARNING}Skipped: {self.skipped}{Colors.ENDC}")
        print(f"{Colors.BOLD}{'=' * 70}{Colors.ENDC}\n")

    def test_initialize(self) -> None:
        """Test server initialization."""
        self.print_test("Server Initialize")
        try:
            result = self.client.initialize()
            assert "protocolVersion" in result
            assert "capabilities" in result
            assert "serverInfo" in result
            assert result["serverInfo"]["name"] == "ClaudeSkills"
            self.print_pass("Server initialized successfully")
        except Exception as e:
            self.print_fail(str(e))

    def test_list_tools(self) -> None:
        """Test listing tools."""
        self.print_test("List Tools")
        try:
            tools = self.client.list_tools()
            assert isinstance(tools, list)
            assert len(tools) > 0

            expected_tools = {
                "skill_server_info",
                "skill_list_all",
                "skill_get_detail",
                "skill_search_index",
                "skill_list_assets",
                "skill_read_asset",
            }

            tool_names = {tool["name"] for tool in tools}
            assert expected_tools.issubset(tool_names)

            self.print_pass(f"Found {len(tools)} tools")

            # Print tool names
            print(f"\n  Available tools:")
            for tool in sorted(tools, key=lambda t: t["name"]):
                print(f"    • {tool['name']}")

        except Exception as e:
            self.print_fail(str(e))

    def test_server_info(self) -> None:
        """Test skill_server_info tool."""
        self.print_test("Server Info Tool")
        try:
            result = self.client.call_tool("skill_server_info")
            assert len(result) > 0
            # Try structuredContent first, fall back to parsing text
            data = result[0].get("structuredContent")
            if not data:
                data = json.loads(result[0]["text"])
            assert data["name"] == "ClaudeSkills"
            assert "skills_dir" in data
            self.print_pass(f"Skills dir: {data['skills_dir']}")
        except Exception as e:
            self.print_fail(str(e))

    def test_list_skills(self) -> None:
        """Test skill_list_all tool."""
        self.print_test("List All Skills")
        try:
            result = self.client.call_tool("skill_list_all")
            assert len(result) > 0
            skills = json.loads(result[0]["text"])
            assert isinstance(skills, list)

            if skills:
                self.print_pass(f"Found {len(skills)} skills")
                print(f"\n  Sample skills:")
                for skill in skills[:5]:
                    print(f"    • {skill['name']}: {skill['description'][:60]}...")
            else:
                self.print_skip("No skills found")

        except Exception as e:
            self.print_fail(str(e))

    def test_get_skill_detail(self) -> None:
        """Test skill_get_detail tool."""
        self.print_test("Get Skill Detail")
        try:
            # First get list of skills
            list_result = self.client.call_tool("skill_list_all")
            skills = json.loads(list_result[0]["text"])

            if not skills:
                self.print_skip("No skills available")
                return

            skill_name = skills[0]["name"]
            result = self.client.call_tool("skill_get_detail", {"name": skill_name})
            assert len(result) > 0
            detail = json.loads(result[0]["text"])
            assert detail["name"] == skill_name
            assert "body" in detail

            self.print_pass(f"Retrieved detail for '{skill_name}'")

        except Exception as e:
            self.print_fail(str(e))

    def test_search_skills(self) -> None:
        """Test skill_search_index tool."""
        self.print_test("Search Skills")
        try:
            result = self.client.call_tool("skill_search_index", {"query": "skill"})
            assert len(result) > 0
            matches = json.loads(result[0]["text"])
            assert isinstance(matches, list)
            self.print_pass(f"Search returned {len(matches)} matches")
        except Exception as e:
            self.print_fail(str(e))

    def test_markdown_output(self) -> None:
        """Test markdown output format."""
        self.print_test("Markdown Output")
        try:
            result = self.client.call_tool("skill_list_all", {"markdown_output": True})
            assert len(result) > 0
            text = result[0]["text"]
            assert isinstance(text, str)
            assert len(text) > 0
            self.print_pass("Markdown output returned successfully")
        except Exception as e:
            self.print_fail(str(e))

    def test_error_handling(self) -> None:
        """Test error handling for invalid requests."""
        self.print_test("Error Handling")
        try:
            error_count = 0

            # Test invalid tool name
            try:
                self.client.call_tool("nonexistent_tool")
                self.print_fail("Expected error for invalid tool name")
                return
            except ValueError:
                error_count += 1

            # Test missing required parameter
            try:
                self.client.call_tool("skill_get_detail", {})
                self.print_fail("Expected error for missing parameter")
                return
            except ValueError:
                error_count += 1

            if error_count == 2:
                self.print_pass("Error handling works correctly")
            else:
                self.print_fail(f"Only {error_count}/2 error cases handled correctly")
        except Exception as e:
            self.print_fail(str(e))

    def run_all_tests(self) -> bool:
        """Run all tests and return success status."""
        self.print_header("MCP Skills Server - Stdio Integration Tests")

        self.test_initialize()
        self.test_list_tools()
        self.test_server_info()
        self.test_list_skills()
        self.test_get_skill_detail()
        self.test_search_skills()
        self.test_markdown_output()
        self.test_error_handling()

        self.print_summary()
        return self.failed == 0


def interactive_mode(client: MCPStdioClient) -> None:
    """Run client in interactive mode."""
    print(f"\n{Colors.HEADER}{Colors.BOLD}Interactive MCP Client{Colors.ENDC}")
    print("Type 'help' for available commands, 'quit' to exit\n")

    # Initialize
    try:
        client.initialize()
        print(f"{Colors.OKGREEN}✓ Connected to server{Colors.ENDC}\n")
    except Exception as e:
        print(f"{Colors.FAIL}✗ Failed to connect: {e}{Colors.ENDC}")
        return

    # Get tools
    tools = client.list_tools()
    tool_names = [t["name"] for t in tools]

    while True:
        try:
            command = input(f"{Colors.BOLD}mcp> {Colors.ENDC}").strip()

            if not command:
                continue

            if command in ("quit", "exit", "q"):
                break

            if command == "help":
                print("\nAvailable commands:")
                print("  help              - Show this help")
                print("  list              - List all tools")
                print("  call <tool> [json] - Call a tool with optional JSON arguments")
                print("  quit              - Exit interactive mode")
                print("\nAvailable tools:")
                for name in sorted(tool_names):
                    print(f"  • {name}")
                print()
                continue

            if command == "list":
                print(f"\nAvailable tools ({len(tools)}):")
                for tool in sorted(tools, key=lambda t: t["name"]):
                    print(f"  • {tool['name']}")
                    print(f"    {tool['description'][:70]}...")
                print()
                continue

            if command.startswith("call "):
                parts = command.split(maxsplit=2)
                tool_name = parts[1] if len(parts) > 1 else ""
                args_json = parts[2] if len(parts) > 2 else "{}"

                if tool_name not in tool_names:
                    print(f"{Colors.FAIL}✗ Unknown tool: {tool_name}{Colors.ENDC}")
                    continue

                try:
                    args = json.loads(args_json)
                    result = client.call_tool(tool_name, args)
                    print(f"\n{Colors.OKGREEN}Result:{Colors.ENDC}")
                    for item in result:
                        if item["type"] == "text":
                            # Try to pretty-print JSON
                            try:
                                data = json.loads(item["text"])
                                print(json.dumps(data, indent=2))
                            except json.JSONDecodeError:
                                print(item["text"])
                    print()
                except json.JSONDecodeError:
                    print(f"{Colors.FAIL}✗ Invalid JSON arguments{Colors.ENDC}")
                except Exception as e:
                    print(f"{Colors.FAIL}✗ Error: {e}{Colors.ENDC}")
                continue

            print(f"{Colors.WARNING}Unknown command: {command}{Colors.ENDC}")
            print("Type 'help' for available commands")

        except KeyboardInterrupt:
            print()
            break
        except EOFError:
            break

    print(f"\n{Colors.OKCYAN}Goodbye!{Colors.ENDC}")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Stdio test client for skills-mcp MCP server"
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Run in interactive mode",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--test",
        "-t",
        help="Run specific test (e.g., list_tools)",
    )
    parser.add_argument(
        "--command",
        "-c",
        default="python -m skills_mcp.server",
        help="Command to start server (default: python -m skills_mcp.server)",
    )

    args = parser.parse_args()

    # Get repository root
    repo_root = Path(__file__).resolve().parents[1]

    # Create client
    client = MCPStdioClient(
        command=args.command.split(),
        cwd=repo_root,
        verbose=args.verbose,
    )

    try:
        client.start()

        if args.interactive:
            interactive_mode(client)
            return 0

        # Run tests
        runner = TestRunner(client)

        if args.test:
            # Run specific test
            test_method = getattr(runner, f"test_{args.test}", None)
            if test_method:
                runner.test_initialize()  # Always initialize first
                test_method()
                runner.print_summary()
            else:
                print(f"{Colors.FAIL}Unknown test: {args.test}{Colors.ENDC}")
                return 1
        else:
            # Run all tests
            success = runner.run_all_tests()
            return 0 if success else 1

    except Exception as e:
        print(f"{Colors.FAIL}Error: {e}{Colors.ENDC}")
        return 1
    finally:
        client.stop()


if __name__ == "__main__":
    sys.exit(main())
