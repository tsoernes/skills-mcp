# Skills MCP — FastMCP stdio server for Claude Agent Skills

This repository provides a FastMCP stdio server that exposes the Anthropic Claude Agent Skills (folders under `skills/`) as MCP tools. It lets MCP-aware clients (e.g., Claude Desktop, other MCP agent runtimes) discover, search, and read skills and their assets programmatically.

The server entrypoint is `mcp_server.py`. It scans the `skills/` directory for `SKILL.md` files conforming to the Agent Skills Spec (YAML frontmatter + Markdown body) and exposes a set of read-only tools.

## Repository layout

- `skills/` — Collection of Claude skills. Each skill must contain a `SKILL.md` with YAML frontmatter per the Agent Skills Spec. Note: this folder is gitignored by default; the server can optionally git-sync it at startup.
- `mcp_server.py` — FastMCP server that exposes skill discovery/search/read as MCP tools over stdio.
- `pyproject.toml` — Project metadata and dependencies (managed via `uv`).

## Requirements

- Python `>=3.11`
- `uv` package manager installed (https://docs.astral.sh/uv/)
- A shell to run commands; examples below assume `fish` (use `source .venv/bin/activate` for bash/zsh).

## Setup with uv

You can either fully sync dependencies from `pyproject.toml` or install in editable mode:

- Create a virtual environment and install:
  - `uv venv`
  - `source .venv/bin/activate.fish`
  - `uv pip install -e .`

- Or perform a one-shot sync:
  - `uv sync`
  - Then activate:
  - `source .venv/bin/activate.fish`

Package name: skills-mcp
The server depends on:
- `fastmcp`
- `pyyaml`

Both are declared in `pyproject.toml`.

## Running the server (stdio)

The server uses stdio transport by default when executed as a script. From the repository root:

- `python skills-mcp/mcp_server.py`
- Or via console script after editable install: `skills-mcp` (starts stdio server)
- CLI/inspection mode: `skills-mcp-cli --list` | `--detail <NAME>` | `--search "<QUERY>"` | `--assets <NAME>` | `--read <NAME> <PATH>` | `--serve`
- Or with module: `python -m skills_mcp.server` (use flags above)

When launched by an MCP client (e.g., Claude Desktop), the client will spawn this script and connect via stdio automatically.

## Server-level documentation

- Name: ClaudeSkills MCP Server
- Purpose: Exposes Anthropic Claude Agent Skills located in the `skills/` folder as MCP tools so agents can discover, search, and read skill guidance and assets.
- Transport: stdio by default.
- Background git sync: On startup, a background thread can clone or pull updates into `skills/`. Configure via environment:
  - `SKILLS_GIT_URL`: git URL for the skills repository (optional)
  - `SKILLS_GIT_BRANCH`: branch name (default: `main`)
  - `SKILLS_DIR`: override skills directory (default: `<repo_root>/skills`)
- Logging: Logs to console and to a rotating file at `logs/skills_mcp_server.log` by default. Override with `LOG_FILE` environment variable.

## Exposed MCP tools

The server registers the following tools:

- `list_skills() -> list[dict]`
  - Lists all discovered skills with brief metadata.
  - Returns entries containing `name`, `description`, `license?`, `allowed_tools?`, `metadata?`, and `path` (relative to `skills/`).

- `get_skill_detail(name: str) -> dict[str, any]`
  - Returns the full parsed content of a single skill by `name`, including the `body` (markdown) and the frontmatter properties.

- `search_skill_index(query: str) -> list[dict]`
  - Case-insensitive substring search across `name`, `description`, and the markdown `body`.
  - Returns brief matches with `name`, `description`, `path`.

- `list_skill_assets(name: str) -> list[dict]`
  - Lists non-`SKILL.md` files inside the skill’s directory (recursively).
  - Returns `path` (relative to skill), `size`, and `mime_type`.

- `read_skill_asset(name: str, path: str, max_bytes: int = 1048576) -> dict[str, any]`
  - Reads a single file inside the given skill.
  - Returns:
    - `encoding`: `text` or `base64`
    - `data`: UTF-8 text or base64-encoded bytes
    - `mime_type`: best-effort MIME type guess
    - `truncated`: `true` if `max_bytes` cut the file

Notes:
- Path traversal is blocked; `path` must remain within the skill directory.
- Text vs. binary detection is based on MIME type and UTF-8 decode capability.
- Large files are truncated to `max_bytes` (defaults to 1 MiB).

## Claude Desktop / MCP client integration

- Configure your MCP client to launch the server script `skills-mcp/mcp_server.py` via stdio.
- If your client supports a configuration object for MCP servers, specify:
  - `transport`: `stdio`
  - `command`: `python`
  - `args`: `["skills-mcp/mcp_server.py"]`
  - Optionally `cwd`: repository root
  - Optionally `env`: relevant environment variables (none required by default)

Different clients format this configuration differently; consult your client’s documentation for the exact shape.

## Validating skills

Each skill must satisfy the Agent Skills Spec:
- Include a `SKILL.md` starting with YAML frontmatter delimited by `---` on its own line at the start and `---` on its own line when it ends.
- Required keys:
  - `name`: hyphen-case; must match the folder name containing the `SKILL.md`
  - `description`: concise guidance for when and how the skill should be used
- Optional keys:
  - `license`
  - `allowed-tools`
  - `metadata`

If parsing fails, the server still returns a placeholder entry for the skill and includes an `error` message under `metadata`.

## Development notes

- Transport: stdio is the default; no extra configuration required.
- Logging: console and rotating file logs at `logs/skills_mcp_server.log` (configurable via `LOG_FILE`); errors parsing invalid skills are reported but do not stop discovery.
- Security: asset reads are constrained to the skill directory; path traversal is rejected.
- MIME types: `guess_type` is used as a best effort; some uncommon types may return `None`.

## Tests

- After creating the venv and syncing deps, run: `pytest -q`.
- Tests live in `tests/` and cover discovery, detail retrieval, asset listing/reading.

## Troubleshooting

- If your environment complains about missing dependencies, ensure you activated the virtual environment created by `uv venv`.
- If editable install fails due to missing `README.md`, ensure this file exists (it should now).
- If your MCP client cannot find or start the server, check that the `command`/`args` paths and working directory are correct and that Python `>=3.11` is used.

## Open questions and potential improvements

To tailor this server to your workflow, it would help to clarify:

- Should we also expose the skills as MCP resources (e.g., each `SKILL.md` and related assets available via `resource://` URIs)?
- Do you want an HTTP/SSE transport option alongside stdio for remote usage?
- Should we add indexing/caching for faster `search_skill_index` on large collections?
- Any access-control requirements (e.g., filtering certain skills, enforcing `allowed-tools`)?
- Should we enforce stricter validation for nested skill structures (e.g., `document-skills/docx`) or allow folder naming exceptions?
- Would you like optional rendering helpers (e.g., HTML previews, markdown normalization, or metadata summaries)?

If you want any of the above, or have specific preferences, let me know and I can extend the server accordingly.