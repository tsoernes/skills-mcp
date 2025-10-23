"""
skills_mcp.server

FastMCP stdio server exposing Anthropic Claude Agent Skills (Agent Skills Spec)
as MCP tools for agents to discover, search, and read skill guidance and assets.

Server-level documentation:
- Purpose: Make skills in the `skills/` directory programmatically accessible to MCP-aware clients.
- Why use it:
  * Agents can list skills with descriptions and metadata
  * Fetch full skill documents (frontmatter + markdown body)
  * Search across skills
  * Enumerate and read skill assets safely
  * Automatically keep skills up to date via background git sync
- Transport: STDIO by default (ideal for clients that spawn the server process)
- Safety: Reject path traversal; text/binary detection; truncation for large reads
- Logging: Console + rotating file logs
- Startup: Optional background git sync of the `skills/` folder

Environment (optional):
- SKILLS_GIT_URL: git URL (e.g., https://github.com/yourorg/skills-repo.git)
- SKILLS_GIT_BRANCH: branch to pull/clone (default: main)
- SKILLS_DIR: override path to the skills directory (default: <repo_root>/skills)
- LOG_FILE: override log file path (default: <repo_root>/logs/skills_mcp_server.log)

Usage:
- As a script:
  python -m skills_mcp.server        # starts stdio server
  python -m skills_mcp.server --help # CLI for inspection without starting server

- As a module within MCP client config (stdio):
  command: python
  args: ["-m", "skills_mcp.server"]

Package: skills_mcp
Entry point: python -m skills_mcp.server
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import threading
from logging.handlers import RotatingFileHandler
from mimetypes import guess_type
from pathlib import Path
from typing import Any

import yaml
from fastmcp import FastMCP


# --- Paths & constants ---
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_DIR = REPO_ROOT / "skills"
DEFAULT_LOG_DIR = REPO_ROOT / "logs"
DEFAULT_LOG_FILE = DEFAULT_LOG_DIR / "skills_mcp_server.log"
SERVER_NAME = "ClaudeSkills"


# --- Logging setup ---
def configure_logging() -> logging.Logger:
    """
    function_purpose: Configure application-wide logging to both console and rotating file.

    - Creates logs directory if needed.
    - Sets formatter and levels.
    - Returns the configured root logger for reuse.
    """
    logger = logging.getLogger(SERVER_NAME)
    logger.setLevel(logging.INFO)

    # Ensure log directory exists
    log_file_env = os.environ.get("LOG_FILE")
    log_file = Path(log_file_env) if log_file_env else DEFAULT_LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s]: %(message)s"
    )

    # Console handler
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    # Rotating file handler (5 files, 5MB each)
    fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5)
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    logger.info("Logging initialized. File: %s", str(log_file))
    return logger


# --- Git sync (background) ---
def _is_git_repo(path: Path) -> bool:
    """
    function_purpose: Detect whether the given path is a git repository.

    Returns True if a .git directory exists within path.
    """
    return (path / ".git").is_dir()


def _git_run(args: list[str], cwd: Path, logger: logging.Logger) -> None:
    """
    function_purpose: Run a git command and log its outcome.

    Executes subprocess without raising errors; logs output/stderr for diagnostics.
    """
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        logger.info("git %s\n%s", " ".join(args), res.stdout.strip())
    except Exception as exc:
        logger.error("Git command failed: git %s (%s)", " ".join(args), exc)


def _git_sync(skills_dir: Path, logger: logging.Logger) -> None:
    """
    function_purpose: Clone or pull the skills repository in the background on server startup.

    Behavior:
    - If skills_dir is a git repo: fetch + pull ff-only.
    - If not a repo and SKILLS_GIT_URL is set: clone depth=1 on branch.
    - If neither condition applies: skip with info.
    """
    git_url = (
        os.environ.get("SKILLS_GIT_URL") or "https://github.com/anthropics/skills"
    ).strip()
    branch = os.environ.get("SKILLS_GIT_BRANCH", "main").strip()

    if _is_git_repo(skills_dir):
        logger.info(
            "Skills directory is a git repo; fetching latest on branch '%s'.", branch
        )
        _git_run(["fetch", "origin"], cwd=skills_dir, logger=logger)
        _git_run(["checkout", branch], cwd=skills_dir, logger=logger)
        _git_run(["pull", "--ff-only", "origin", branch], cwd=skills_dir, logger=logger)
        return

    if git_url:
        # If directory exists but is not a repo, attempt a shallow clone into it
        if not skills_dir.exists():
            skills_dir.mkdir(parents=True, exist_ok=True)
        parent = skills_dir.parent
        target_name = skills_dir.name

        logger.info(
            "Cloning skills repo '%s' (branch '%s') into '%s'.",
            git_url,
            branch,
            str(skills_dir),
        )
        # If directory is non-empty, clone into temp then move/replace could be added;
        # for simplicity, clone directly targeting the directory.
        _git_run(
            ["clone", "--depth=1", "-b", branch, git_url, target_name],
            cwd=parent,
            logger=logger,
        )
    else:
        logger.info(
            "No SKILLS_GIT_URL provided; skipping clone. Using local skills directory."
        )


def start_background_git_sync(skills_dir: Path, logger: logging.Logger) -> None:
    """
    function_purpose: Launch a non-blocking thread that performs git sync of skills directory.

    Keeps server startup fast while syncing in the background.
    """
    t = threading.Thread(
        target=_git_sync,
        name="GitSyncThread",
        args=(skills_dir, logger),
        daemon=True,
    )
    t.start()
    logger.info("Background git sync started.")


# --- Skills parsing and utilities ---
def _parse_frontmatter_and_body(text: str) -> tuple[dict[str, Any], str]:
    """
    function_purpose: Parse YAML frontmatter delimited by '---' lines, followed by markdown body.

    Returns a (frontmatter_dict, body_text) tuple.
    """
    lines = text.splitlines(keepends=False)
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must begin with a '---' line for YAML frontmatter")

    fm_lines: list[str] = []
    idx = 1
    while idx < len(lines) and lines[idx].strip() != "---":
        fm_lines.append(lines[idx])
        idx += 1

    if idx >= len(lines) or lines[idx].strip() != "---":
        raise ValueError("YAML frontmatter must end with a '---' line")

    fm_text = "\n".join(fm_lines)
    body = "\n".join(lines[idx + 1 :])

    fm = yaml.safe_load(fm_text) or {}
    if not isinstance(fm, dict):
        raise ValueError("YAML frontmatter must parse to a mapping")
    return fm, body


def parse_skill_md(md_path: Path, skills_dir: Path) -> dict[str, Any]:
    """
    function_purpose: Parse a SKILL.md file to structured data per Agent Skills Spec.

    Enforces:
    - 'name' (hyphen-case) and 'description' strings are required
    - immediate directory name must match 'name'
    """
    text = md_path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter_and_body(text)

    name = fm.get("name")
    description = fm.get("description")
    license_ = fm.get("license")
    allowed_tools = fm.get("allowed-tools")
    metadata = fm.get("metadata")

    if not isinstance(name, str) or not name:
        raise ValueError(
            "frontmatter 'name' is required and must be a non-empty string"
        )
    if not isinstance(description, str) or not description:
        raise ValueError(
            "frontmatter 'description' is required and must be a non-empty string"
        )

    dir_name = md_path.parent.name
    if dir_name != name:
        raise ValueError(
            f"skill directory '{dir_name}' must match frontmatter name '{name}'"
        )

    rel_path = str(md_path.relative_to(skills_dir))
    return {
        "name": name,
        "description": description,
        "license": license_ if isinstance(license_, str) else None,
        "allowed_tools": allowed_tools if isinstance(allowed_tools, list) else None,
        "metadata": metadata if isinstance(metadata, dict) else None,
        "path": rel_path,
        "body": body,
    }


def iter_skill_md_paths(skills_dir: Path) -> list[Path]:
    """
    function_purpose: Locate all SKILL.md files under skills_dir recursively.
    """
    if not skills_dir.exists():
        return []
    return list(skills_dir.rglob("SKILL.md"))


def discover_skills(
    skills_dir: Path, logger: logging.Logger | None = None
) -> list[dict[str, Any]]:
    """
    function_purpose: Discover and parse all skills under the skills_dir.

    Returns list of parsed skill dicts; invalid skills included with error metadata.
    """
    skills: list[dict[str, Any]] = []
    for md_path in iter_skill_md_paths(skills_dir):
        try:
            data = parse_skill_md(md_path, skills_dir)
            skills.append(data)
        except Exception as exc:
            rel_path = str(md_path.relative_to(skills_dir))
            if logger:
                logger.error("Failed parsing %s: %s", rel_path, exc)
            skills.append(
                {
                    "name": md_path.parent.name,
                    "description": f"Invalid SKILL.md: {exc}",
                    "license": None,
                    "allowed_tools": None,
                    "metadata": {"error": str(exc), "path": rel_path},
                    "path": rel_path,
                    "body": "",
                }
            )
    skills.sort(key=lambda s: (s.get("name") or "", s.get("path") or ""))
    return skills


def get_skill(skills_dir: Path, name: str) -> dict[str, Any]:
    """
    function_purpose: Retrieve full skill details by its name (hyphen-case).
    """
    for skill in discover_skills(skills_dir):
        if skill.get("name") == name:
            return skill
    raise ValueError(f"skill '{name}' not found")


def search_skills(skills_dir: Path, query: str) -> list[dict[str, Any]]:
    """
    function_purpose: Case-insensitive substring search across name, description, and body.

    Returns brief matches with {name, description, path}.
    """
    q = (query or "").strip().lower()
    results: list[dict[str, Any]] = []
    if not q:
        return results
    for s in discover_skills(skills_dir):
        hay = "\n".join(
            [
                str(s.get("name", "")),
                str(s.get("description", "")),
                str(s.get("body", "")),
            ]
        ).lower()
        if q in hay:
            results.append(
                {
                    "name": s.get("name"),
                    "description": s.get("description"),
                    "path": s.get("path"),
                }
            )
    return results


def skill_dir_for_name(skills_dir: Path, name: str) -> Path:
    """
    function_purpose: Resolve the directory path for a skill by its name.

    Parses SKILL.md entries to find the skill's folder reliably.
    """
    for md_path in iter_skill_md_paths(skills_dir):
        try:
            data = parse_skill_md(md_path, skills_dir)
            if data.get("name") == name:
                return md_path.parent
        except Exception:
            # Skip invalid while resolving
            continue
    raise ValueError(f"skill '{name}' not found")


def list_skill_assets(skills_dir: Path, name: str) -> list[dict[str, Any]]:
    """
    function_purpose: Enumerate non-SKILL.md files within a skill directory.

    Returns dicts: {path, size, mime_type} with path relative to the skill folder.
    """
    sdir = skill_dir_for_name(skills_dir, name)
    assets: list[dict[str, Any]] = []
    for f in [p for p in sdir.rglob("*") if p.is_file()]:
        if f.name == "SKILL.md":
            continue
        rel = f.relative_to(sdir).as_posix()
        try:
            size = f.stat().st_size
        except OSError:
            size = None
        mime, _ = guess_type(f.name)
        assets.append({"path": rel, "size": size, "mime_type": mime})
    assets.sort(key=lambda x: x["path"])
    return assets


def _is_text_data(data: bytes, mime_type: str | None) -> bool:
    """
    function_purpose: Determine if byte content should be treated as text.

    Considers MIME type and UTF-8 decodability.
    """
    if mime_type and (
        mime_type.startswith("text/")
        or mime_type
        in {
            "application/json",
            "application/xml",
            "application/yaml",
            "application/x-yaml",
            "application/toml",
            "application/javascript",
        }
    ):
        return True
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def read_skill_asset(
    skills_dir: Path,
    name: str,
    rel_path: str,
    max_bytes: int = 8_388_608,
) -> dict[str, Any]:
    """
    function_purpose: Safely read a file within a skill directory, returning text or base64 data.

    Returns: {
      "encoding": "text" | "base64",
      "data": str,
      "mime_type": str | None,
      "truncated": bool
    }
    """
    sdir = skill_dir_for_name(skills_dir, name)
    file_path = (sdir / rel_path).resolve()

    # Prevent path traversal
    if sdir not in file_path.parents and file_path != sdir:
        raise ValueError("path must be within the skill directory")

    if not file_path.exists() or not file_path.is_file():
        raise ValueError(f"file not found: {rel_path}")

    mime, _ = guess_type(file_path.name)
    raw = file_path.read_bytes()
    truncated = False
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True

    if _is_text_data(raw, mime):
        data = raw.decode("utf-8", errors="replace")
        return {
            "encoding": "text",
            "data": data,
            "mime_type": mime,
            "truncated": truncated,
        }
    else:
        b64 = base64.b64encode(raw).decode("ascii")
        return {
            "encoding": "base64",
            "data": b64,
            "mime_type": mime,
            "truncated": truncated,
        }


# --- FastMCP server and tools ---
def _resolve_skills_dir() -> Path:
    """
    function_purpose: Resolve skills directory from environment or default location.
    """
    env_dir = os.environ.get("SKILLS_DIR")
    return Path(env_dir).resolve() if env_dir else DEFAULT_SKILLS_DIR


def _server_description() -> str:
    """
    function_purpose: Provide a server-level description that clients can display.
    """
    return (
        "ClaudeSkills MCP Server: exposes Anthropic Claude Agent Skills located in the 'skills/' "
        "folder as MCP tools. Use this to discover, search, and read skill guidance and assets. "
        "It also supports optional background git sync for keeping skills updated."
    )


mcp = FastMCP(
    SERVER_NAME,
    instructions=(
        "ClaudeSkills MCP Server\n"
        "\n"
        "Purpose:\n"
        "- Expose Anthropic Claude Agent Skills located in the 'skills/' folder as MCP tools so agents can\n"
        "  discover, search, and read skill guidance and assets programmatically.\n"
        "\n"
        "Why use it:\n"
        "- Agents can list skills with descriptions and metadata\n"
        "- Fetch full skill documents (frontmatter + markdown body)\n"
        "- Search across skills (name, description, and markdown body)\n"
        "- Enumerate and read skill assets safely with path traversal protection and size limits\n"
        "- Automatically keep skills up to date via background git sync on startup\n"
        "- When you discover corrections, better examples, or scripts, use store_skill_note(name, title, content)\n"
        "  to append a note under the skill (additions only) so future improvements can be incorporated.\n"
        "\n"
        "Transports:\n"
        "- STDIO by default (ideal for MCP clients that spawn a server process)\n"
        "\n"
        "Startup behavior:\n"
        "- Background git sync of 'skills' directory: if it's a git repo, fetch/pull; otherwise shallow clone\n"
        "  using SKILLS_GIT_URL (default https://github.com/anthropics/skills) and SKILLS_GIT_BRANCH (default 'main').\n"
        "\n"
        "Environment configuration:\n"
        "- SKILLS_GIT_URL       : git URL for skills repo (default: https://github.com/anthropics/skills)\n"
        "- SKILLS_GIT_BRANCH    : git branch to pull/clone (default: main)\n"
        "- SKILLS_DIR           : override skills directory (default: <repo_root>/skills)\n"
        "- LOG_FILE             : override rotating log file path (default: <repo_root>/logs/skills_mcp_server.log)\n"
        "\n"
        "Safety & limits:\n"
        "- Asset reads reject path traversal and cap bytes via 'max_bytes' (default 8 MiB). Text vs binary detection\n"
        "  uses MIME type and UTF-8 decodability; returns either text or base64 content.\n"
        "\n"
        "Exposed tools:\n"
        "- server_info(): server name, description, skills_dir, transport\n"
        "- list_skills(): brief skill metadata (name, description, license?, allowed_tools?, metadata?, path)\n"
        "- get_skill_detail(name): full parsed frontmatter + markdown body for a skill\n"
        "- search_skill_index(query): substring search across name/description/body, returns brief matches\n"
        "- list_skill_assets_tool(name): non-SKILL.md files inside a skill (path, size, mime_type)\n"
        "- read_skill_asset_tool(name, path, max_bytes): read an asset within a skill (text/base64 + mime_type + truncated)\n"
        "\n"
        "Notes:\n"
        "- Skills must adhere to Agent Skills Spec (SKILL.md with YAML frontmatter: name, description).\n"
        "- Immediate directory name must match 'name' in frontmatter (e.g., document-skills/docx with name: docx).\n"
        "- Invalid SKILL.md entries are surfaced with error diagnostics in metadata but do not stop discovery.\n"
    ),
)


@mcp.tool
def server_info() -> dict[str, Any]:
    """
    function_purpose: Return server-level documentation including purpose and usage.

    Description:
    - Provides an overview of the ClaudeSkills MCP Server, its transport mode, and where skills are loaded from.
    - Useful for clients to show contextual info and help users understand capabilities and configuration.

    Returns:
    - name: str                    Server name
    - description: str             High-level description of server purpose and capabilities
    - skills_dir: str              Absolute path to the skills directory in use
    - transport: str               Transport used by the server (e.g., "stdio")

    Usage:
    - Call this tool once when connecting, then cache/show details in the client UI or logs.
    """
    skills_dir = _resolve_skills_dir()
    return {
        "name": SERVER_NAME,
        "description": _server_description(),
        "skills_dir": str(skills_dir),
        "transport": "stdio",
    }


@mcp.tool
def list_skills() -> list[dict[str, Any]]:
    """
    function_purpose: List available skills with brief metadata (excluding body).

    Description:
    - Enumerates all discovered skills from the skills directory and returns summary metadata.
    - Excludes the markdown body for compact listing; use get_skill_detail for full content.

    Returns:
    - List of dict entries containing:
      - name: str
      - description: str
      - license: str | None
      - allowed_tools: list[str] | None
      - metadata: dict[str, Any] | None
      - path: str (relative path to SKILL.md within skills dir)

    Usage:
    - Use this to present a catalog of available skills to the agent or user.
    """
    skills_dir = _resolve_skills_dir()
    skills = discover_skills(skills_dir)
    return [
        {
            k: v
            for k, v in s.items()
            if k
            in {"name", "description", "license", "allowed_tools", "metadata", "path"}
        }
        for s in skills
    ]


@mcp.tool
def get_skill_detail(name: str) -> dict[str, Any]:
    """
    function_purpose: Get full parsed details for a specific skill by name (frontmatter + body).

    Description:
    - Returns the complete parsed skill including frontmatter fields and the markdown body content.

    Args:
    - name: str  The hyphen-case name of the skill (must match the skill directory name)

    Returns:
    - dict containing:
      - name, description, license?, allowed_tools?, metadata?, path, body (markdown)

    Usage:
    - Use this when the agent needs the full guidance text and metadata for a skill.
    """
    skills_dir = _resolve_skills_dir()
    return get_skill(skills_dir, name)


@mcp.tool
def search_skill_index(query: str) -> list[dict[str, Any]]:
    """
    function_purpose: Search skills by case-insensitive substring across name, description, and body.

    Description:
    - Performs a simple substring search across the parsed name, description, and body for each skill.

    Args:
    - query: str (case-insensitive substring)

    Returns:
    - List of brief matches with:
      - name: str
      - description: str
      - path: str (relative to skills dir)

    Usage:
    - Use this to quickly locate relevant skills by topic or keywords.
    """
    skills_dir = _resolve_skills_dir()
    return search_skills(skills_dir, query)


@mcp.tool
def list_skill_assets_tool(name: str) -> list[dict[str, Any]]:
    """
    function_purpose: List non-SKILL.md files within a skill folder (recursive).

    Description:
    - Enumerates files inside a specific skill directory, excluding SKILL.md, recursively.
    - Useful for discovering supporting artifacts, reference materials, templates, and helper scripts that belong to a skill.

    Args:
    - name: str  The hyphen-case name of the skill whose assets to list.

    Returns:
    - list[dict[str, Any]] containing:
      - path: str        Relative path within the skill directory
      - size: int | None File size in bytes if available
      - mime_type: str | None  Best-effort MIME type guess

    Usage:
    - Call before reading assets to present available files to the agent or user.
    - For reading actual content, use read_skill_asset_tool() with the returned path.
    """
    skills_dir = _resolve_skills_dir()
    return list_skill_assets(skills_dir, name)


@mcp.tool
def read_skill_asset_tool(
    name: str, path: str, max_bytes: int = 8_388_608
) -> dict[str, Any]:
    """
    function_purpose: Read a specific asset file within a skill (returns text or base64 data).

    Description:
    - Safely reads an asset inside a skill directory, preventing path traversal and limiting size via max_bytes.
    - Returns UTF-8 text when possible, otherwise base64-encoded bytes, including a MIME type guess and truncation flag.

    Args:
    - name: str       The hyphen-case skill name (must match skill directory)
    - path: str       Relative file path within the skill directory
    - max_bytes: int  Maximum number of bytes to read (default: 8_388_608)

    Returns:
    - dict[str, Any] with:
      - encoding: "text" | "base64"
      - data: str                 UTF-8 text or base64 string
      - mime_type: str | None     Best-effort MIME type guess
      - truncated: bool           True if content was cut at max_bytes

    Usage:
    - Use after listing assets to fetch the content of a specific file for analysis or display.
    - If the asset is large, consider increasing max_bytes or reading only required portions.
    """
    skills_dir = _resolve_skills_dir()
    return read_skill_asset(skills_dir, name, path, max_bytes)


@mcp.tool
def store_skill_note(name: str, title: str, content: str) -> dict[str, Any]:
    """
    function_purpose: Append a new note to a skill capturing learnings, improvements, and scripts.

    Description:
    - Safely stores additive notes related to a skill (no edits to existing files). Use this to record
      observations, corrections, suggested improvements, and example scripts discovered while using the skill.
    - Encourages iterative refinement: if documentation turns out inaccurate or incomplete, add a note that
      clarifies, extends, or proposes better approaches. Over time, these notes can guide maintainers to
      improve the canonical SKILL.md.

    Constraints:
    - Additions only. This tool never edits existing files; it only creates new note files.
    - Notes are stored under a dedicated '_notes' directory within the skill folder.

    Args:
    - name: str    The hyphen-case skill name (must match skill directory)
    - title: str   A short, descriptive title for the note
    - content: str The body of the note (Markdown supported)

    Returns:
    - dict[str, Any] with:
      - path: str         Relative path to the created note within the skill directory
      - created: bool     True on success
      - message: str      Status message
    """
    from datetime import datetime

    skills_dir = _resolve_skills_dir()
    sdir = skill_dir_for_name(skills_dir, name)
    notes_dir = sdir / "_notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    # Simple slugification for filename safety
    def _slugify(text: str) -> str:
        cleaned = []
        for ch in text.strip():
            if ch.isalnum():
                cleaned.append(ch.lower())
            elif ch in (" ", "-", "_"):
                cleaned.append("-")
            else:
                cleaned.append("")
        slug = "".join(cleaned).strip("-")
        return slug or "note"

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    slug = _slugify(title)[:80]
    filename = f"{ts}-{slug}.md"
    note_path = notes_dir / filename

    # Exclusive create to prevent overwrites
    fm = [
        "---",
        f'title: "{title}"',
        f"created_at: {ts}",
        "kind: note",
        "---",
        "",
    ]
    body = "\n".join(fm) + content.rstrip() + "\n"

    try:
        with open(note_path, "x", encoding="utf-8") as f:
            f.write(body)
        rel = note_path.relative_to(sdir).as_posix()
        return {"path": rel, "created": True, "message": "Note stored"}
    except FileExistsError:
        # Extremely unlikely due to timestamp; retry with suffix
        alt = notes_dir / f"{ts}-{slug}-1.md"
        with open(alt, "x", encoding="utf-8") as f:
            f.write(body)
        rel = alt.relative_to(sdir).as_posix()
        return {"path": rel, "created": True, "message": "Note stored (with suffix)"}
    except Exception as exc:
        return {"path": "", "created": False, "message": f"Failed to store note: {exc}"}


# --- Entry points ---
def run() -> None:
    """
    function_purpose: Entry point to start the MCP stdio server.

    - Configures logging
    - Starts background git sync
    - Runs FastMCP stdio server
    """
    logger = configure_logging()
    skills_dir = _resolve_skills_dir()
    logger.info("Server starting with skills_dir=%s", str(skills_dir))
    start_background_git_sync(skills_dir, logger)
    mcp.run()  # stdio transport by default


def cli_main() -> None:
    """
    function_purpose: CLI for inspecting skills without starting the MCP server.

    Usage:
      python -m skills_mcp.server --list
      python -m skills_mcp.server --detail <NAME>
      python -m skills_mcp.server --search "<QUERY>"
      python -m skills_mcp.server --assets <NAME>
      python -m skills_mcp.server --read <NAME> <PATH> [--max-bytes N]
    """
    import argparse
    import json

    logger = configure_logging()
    skills_dir = _resolve_skills_dir()

    parser = argparse.ArgumentParser(
        prog="skills_mcp.server",
        description="Inspect Claude skills (Agent Skills Spec) or start stdio MCP server.",
    )
    parser.add_argument(
        "--list", action="store_true", help="List all discovered skills and exit"
    )
    parser.add_argument(
        "--detail", metavar="NAME", help="Show full details for a specific skill name"
    )
    parser.add_argument("--search", metavar="QUERY", help="Search skills by substring")
    parser.add_argument(
        "--assets", metavar="NAME", help="List non-SKILL.md assets inside the skill"
    )
    parser.add_argument(
        "--read",
        nargs=2,
        metavar=("NAME", "PATH"),
        help="Read asset PATH within the skill NAME",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=8_388_608,
        help="Maximum bytes to read for assets",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start MCP stdio server (default when no flags used)",
    )

    args = parser.parse_args()

    if args.list:
        logger.info("Listing skills...")
        skills = discover_skills(skills_dir, logger=logger)
        result = [
            {
                k: v
                for k, v in s.items()
                if k
                in {
                    "name",
                    "description",
                    "license",
                    "allowed_tools",
                    "metadata",
                    "path",
                }
            }
            for s in skills
        ]
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if args.detail:
        logger.info("Detail for skill: %s", args.detail)
        print(
            json.dumps(get_skill(skills_dir, args.detail), indent=2, ensure_ascii=False)
        )
        return

    if args.search:
        logger.info("Search query: %s", args.search)
        print(
            json.dumps(
                search_skills(skills_dir, args.search), indent=2, ensure_ascii=False
            )
        )
        return

    if args.assets:
        logger.info("Listing assets for skill: %s", args.assets)
        print(
            json.dumps(
                list_skill_assets(skills_dir, args.assets), indent=2, ensure_ascii=False
            )
        )
        return

    if args.read:
        name, rel_path = args.read
        logger.info("Reading asset: skill=%s path=%s", name, rel_path)
        payload = read_skill_asset(skills_dir, name, rel_path, args.max_bytes)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    # Default: start server
    run()


if __name__ == "__main__":
    cli_main()
