# Stdio Integration Testing for Skills-MCP

This directory contains comprehensive stdio integration tests for the skills-mcp MCP server. These tests validate the full protocol stack including server startup, MCP protocol communication, tool discovery, and tool invocation over stdio transport.

## Test Files

### `test_stdio_integration.py`
Pytest-based integration test suite that can be run as part of automated testing pipelines.

**Features:**
- Full MCP protocol validation
- Server startup and initialization testing
- Tool discovery and schema validation
- Individual tool invocation tests
- Error handling and edge case validation
- Security testing (path traversal, etc.)
- Concurrent request handling

**Usage:**
```bash
# Run all stdio integration tests
pytest tests/test_stdio_integration.py -v

# Run specific test
pytest tests/test_stdio_integration.py::test_server_starts_and_initializes -v

# Run with output capture disabled (see all logs)
pytest tests/test_stdio_integration.py -v -s

# Run with coverage
pytest tests/test_stdio_integration.py --cov=skills_mcp --cov-report=html
```

### `stdio_test_client.py`
Standalone interactive and scripted test client that can be run directly for manual testing and debugging.

**Features:**
- Automated test suite with colored output
- Interactive REPL mode for manual testing
- Verbose logging mode for debugging
- Individual test execution
- Human-friendly output formatting

**Usage:**
```bash
# Run all automated tests
python tests/stdio_test_client.py

# Run with verbose output (shows JSON-RPC messages)
python tests/stdio_test_client.py --verbose

# Run specific test
python tests/stdio_test_client.py --test list_tools

# Interactive mode
python tests/stdio_test_client.py --interactive

# Custom server command
python tests/stdio_test_client.py --command "python -m skills_mcp.server"
```

## Interactive Mode

The standalone test client includes an interactive REPL for manual testing:

```bash
python tests/stdio_test_client.py --interactive
```

**Interactive commands:**
- `help` - Show available commands
- `list` - List all available tools
- `call <tool> [json]` - Call a tool with optional JSON arguments
- `quit` - Exit interactive mode

**Examples:**
```
mcp> list
mcp> call skill_server_info
mcp> call skill_list_all {"markdown_output": true}
mcp> call skill_get_detail {"name": "mcp-builder"}
mcp> call skill_search_index {"query": "python"}
```

## Test Coverage

The test suite covers:

### Protocol Tests
- ✓ Server startup and initialization
- ✓ MCP protocol version negotiation
- ✓ Server capabilities discovery
- ✓ Tool list retrieval
- ✓ Tool schema validation

### Tool Tests
- ✓ `skill_server_info` - Server metadata
- ✓ `skill_list_all` - List all skills
- ✓ `skill_get_detail` - Get skill details
- ✓ `skill_search_index` - Search skills
- ✓ `skill_list_assets` - List skill assets
- ✓ `skill_list_notes` - List skill notes
- ✓ `skill_read_asset` - Read asset files

### Output Format Tests
- ✓ JSON output format
- ✓ Markdown output format
- ✓ Parameter validation

### Security Tests
- ✓ Path traversal prevention
- ✓ Invalid skill name handling
- ✓ Missing parameter validation

### Error Handling Tests
- ✓ Invalid tool names
- ✓ Missing required parameters
- ✓ Non-existent skills
- ✓ Server error propagation

### Performance Tests
- ✓ Large response handling
- ✓ Multiple sequential requests
- ✓ Concurrent request handling

## Architecture

### MCPStdioClient Class

Both test files include an `MCPStdioClient` class that implements the MCP protocol over stdio:

**Key methods:**
- `start(timeout=10.0)` - Spawn the server process and wait for startup confirmation
- `stop()` - Gracefully terminate the server
- `initialize()` - Send MCP initialize request and notifications/initialized
- `list_tools()` - Retrieve available tools
- `call_tool(name, arguments)` - Invoke a tool

**Protocol implementation:**
- JSON-RPC 2.0 message format
- Line-based message framing
- Request/response correlation by ID
- Error handling and propagation
- Intelligent startup detection via stderr monitoring
- Timeout-based failure detection with error reporting

### TestRunner Class (stdio_test_client.py)

Provides a test framework with:
- Colored output for pass/fail/skip
- Test statistics tracking
- Descriptive test reporting
- Individual test execution

## Prerequisites

```bash
# Install dependencies
uv sync

# Activate virtual environment
source .venv/bin/activate.fish  # or .venv/bin/activate for bash/zsh

# Ensure skills directory exists
ls skills/
```

## Troubleshooting

### Server won't start
- Check that `python -m skills_mcp.server` works manually
- Verify the virtual environment is activated
- Check for port conflicts or resource issues
- Run with `--verbose` to see server stderr output and startup messages

### Tests fail with "No response from server"
- The client waits up to 10s for server startup by monitoring stderr
- Check stderr output for server initialization errors (automatically captured)
- Run with `--verbose` to see detailed protocol messages and stderr monitoring
- If server startup is genuinely slow, increase timeout: `client.start(timeout=20.0)`

### "No skills available" errors
- Ensure the `skills/` directory exists and contains SKILL.md files
- Check that skills conform to the Agent Skills Spec
- Verify permissions on the skills directory

### JSON parsing errors
- Enable verbose mode to see raw JSON-RPC messages
- Check server logs at `logs/skills_mcp_server.log`
- Verify the MCP protocol version matches

## Development

### Adding New Tests

To add a new test to the pytest suite:

```python
def test_new_feature(mcp_client: MCPStdioClient) -> None:
    """Test description."""
    mcp_client.initialize()
    result = mcp_client.call_tool("tool_name", {"param": "value"})
    assert len(result) > 0
    # Add assertions
```

To add a new test to the standalone client:

```python
def test_new_feature(self) -> None:
    """Test description."""
    self.print_test("Test Name")
    try:
        result = self.client.call_tool("tool_name", {"param": "value"})
        assert len(result) > 0
        self.print_pass("Test passed")
    except Exception as e:
        self.print_fail(str(e))
```

### Debugging Tips

1. **Use verbose mode** to see all JSON-RPC messages:
   ```bash
   python tests/stdio_test_client.py --verbose
   ```

2. **Use interactive mode** to manually test tool calls:
   ```bash
   python tests/stdio_test_client.py --interactive
   ```

3. **Check server logs** at `logs/skills_mcp_server.log`

4. **Run pytest with output** to see all print statements:
   ```bash
   pytest tests/test_stdio_integration.py -v -s
   ```

5. **Test individual functions** for quick iteration:
   ```bash
   pytest tests/test_stdio_integration.py::test_skill_list_all -v -s
   ```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Stdio Integration Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install uv
          uv sync
      - name: Run integration tests
        run: |
          source .venv/bin/activate
          pytest tests/test_stdio_integration.py -v
```

## Performance Benchmarks

Typical test execution times:
- Full pytest suite: ~18-23 seconds (18 tests)
- Individual test: ~0.5-1 seconds
- Interactive mode startup: <1 second (intelligent startup detection)
- Server startup detection: <1 second (monitors stderr for ready indicators)

Performance can vary based on:
- Number of skills in the repository
- System resources
- I/O performance
- Server initialization time (git sync, etc.)

## Startup Detection

The test client intelligently detects when the FastMCP server is ready by monitoring stderr output for startup indicators:

- **Startup indicators**: "Starting MCP server", "FastMCP", "Server starting with skills_dir"
- **Error detection**: Automatically detects "error:", "fatal:", "traceback", "exception" in logs
- **Timeout handling**: Configurable timeout (default 10s) with detailed error reporting
- **No arbitrary sleeps**: Uses threading to monitor stderr in real-time

This approach eliminates race conditions and provides immediate feedback if the server fails to start.

## Related Documentation

- [README.md](../README.md) - Main project documentation
- [Agent Skills Spec](https://github.com/anthropics/skills) - Skill format specification
- [MCP Protocol](https://modelcontextprotocol.io/) - MCP protocol documentation
- [FastMCP](https://github.com/jlowin/fastmcp) - FastMCP framework documentation