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
- USER_SKILLS_DIR: override path to user-skills overlay directory (default: <repo_root>/user-skills)
- LOG_FILE: override log file path (default: <repo_root>/logs/skills_mcp_server.log)

User Skills Directory:
- User-created notes and assets can be placed in <repo_root>/user-skills/<skill-name>/notes/
- These overlay onto Anthropic skills and are included when fetching skill details
- Allows tracking user content in git while keeping Anthropic skills separate
- Example: user-skills/mcp-builder/notes/my-note.md will be included with mcp-builder skill

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
DEFAULT_USER_SKILLS_DIR = REPO_ROOT / "user-skills"
DEFAULT_LOG_DIR = REPO_ROOT / "logs"
DEFAULT_LOG_FILE = DEFAULT_LOG_DIR / "skills_mcp_server.log"
DEFAULT_TRASH_DIR = REPO_ROOT / "trash"
DEFAULT_OPS_LOG_DIR = REPO_ROOT / "logs"
DEFAULT_OPS_LOG_FILE = DEFAULT_OPS_LOG_DIR / "skills_mcp_operations.log"
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


def _resolve_trash_dir() -> Path:
    """
    function_purpose: Resolve the trash directory where deleted skills/assets are moved.

    Uses DEFAULT_TRASH_DIR by default; can be overridden via TRASH_DIR env var.
    Ensures the directory exists.
    """
    trash_env = os.environ.get("TRASH_DIR")
    trash_dir = Path(trash_env).resolve() if trash_env else DEFAULT_TRASH_DIR
    trash_dir.mkdir(parents=True, exist_ok=True)
    return trash_dir


def _log_operation(op: str, payload: dict[str, Any]) -> None:
    """
    function_purpose: Append a single JSON line describing a destructive operation.

    This is used for trashing skills and assets so that actions are auditable.
    """
    import json
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    record = {"ts": ts, "op": op}
    record.update(payload)

    try:
        DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(DEFAULT_OPS_LOG_FILE, "a", encoding="utf-8") as f:
            _ = f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # Logging must not break the main operation; swallow errors.
        logging.getLogger(SERVER_NAME).warning(
            "Failed to write operation log entry", exc_info=True
        )


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


def get_skill(
    skills_dir: Path, name: str, include_notes: bool = True
) -> dict[str, Any]:
    """
    function_purpose: Retrieve full skill details by its name (hyphen-case).

    Args:
    - name: str             Skill name
    - include_notes: bool   If True (default), append notes from _notes/ to body

    Returns dict with skill details. If include_notes=True, the body field will have
    notes appended in markdown format for complete context.
    """
    for skill in discover_skills(skills_dir):
        if skill.get("name") == name:
            if include_notes:
                # Append notes to the body from multiple sources:
                # 1. Skill's own _notes/ and notes/ directories
                # 2. User overlay from user-skills/<skill-name>/notes/
                skill_root = skill_dir_for_name(skills_dir, name)
                user_skills_dir = _resolve_user_skills_dir()
                note_files: list[Path] = []

                # Check skill's own notes directories
                for notes_dirname in ["_notes", "notes"]:
                    notes_dir = skill_root / notes_dirname
                    if notes_dir.exists() and notes_dir.is_dir():
                        note_files.extend(
                            [f for f in notes_dir.rglob("*") if f.is_file()]
                        )

                # Check user-skills overlay directory
                user_skill_dir = user_skills_dir / name
                if user_skill_dir.exists():
                    for notes_dirname in ["_notes", "notes"]:
                        user_notes_dir = user_skill_dir / notes_dirname
                        if user_notes_dir.exists() and user_notes_dir.is_dir():
                            note_files.extend(
                                [f for f in user_notes_dir.rglob("*") if f.is_file()]
                            )

                if note_files:
                    # Sort all notes together
                    note_files = sorted(note_files)
                    notes_section = ["\n\n---\n\n# Notes\n"]
                    notes_section.append(
                        "\nThe following notes contain learnings, corrections, improvements, and examples discovered while using this skill:\n"
                    )
                    for note_file in note_files:
                        try:
                            note_content = note_file.read_text(encoding="utf-8")
                            # Try to get relative path from skill root first, then user-skills root
                            try:
                                rel_path = note_file.relative_to(skill_root).as_posix()
                            except ValueError:
                                try:
                                    rel_path = (
                                        f"user-skills/{name}/"
                                        + note_file.relative_to(
                                            user_skill_dir
                                        ).as_posix()
                                    )
                                except ValueError:
                                    rel_path = note_file.name
                            notes_section.append(
                                f"\n## Note: {rel_path}\n\n{note_content}\n"
                            )
                        except Exception:
                            # Skip notes that can't be read
                            pass
                    skill["body"] = skill.get("body", "") + "".join(notes_section)
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


def _is_anthropic_skill(skills_dir: Path, name: str) -> bool:
    """
    function_purpose: Determine if a skill is part of the Anthropic/bundled skills set.

    A skill is considered Anthropic/bundled if its SKILL.md lives under the primary skills_dir.
    """
    try:
        sdir = skill_dir_for_name(skills_dir, name)
    except Exception:
        return False
    # Anthropic/bundled skills live directly under skills_dir; user skills may live elsewhere.
    try:
        return skills_dir.resolve() in sdir.resolve().parents
    except Exception:
        return False


def list_skill_assets(skills_dir: Path, name: str) -> list[dict[str, Any]]:
    """
    function_purpose: Enumerate non-SKILL.md files within a skill directory and user-skills overlay.

    Returns dicts: {path, size, mime_type} with path relative to the skill folder.
    Includes assets from both the skill's directory and user-skills overlay.
    """
    sdir = skill_dir_for_name(skills_dir, name)
    user_skills_dir = _resolve_user_skills_dir()
    assets: list[dict[str, Any]] = []

    # Get assets from skill's own directory
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

    # Get assets from user-skills overlay
    user_skill_dir = user_skills_dir / name
    if user_skill_dir.exists() and user_skill_dir.is_dir():
        for f in [p for p in user_skill_dir.rglob("*") if p.is_file()]:
            try:
                rel = f"user-skills/{name}/" + f.relative_to(user_skill_dir).as_posix()
            except ValueError:
                rel = f.name
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
    function_purpose: Safely read a file within a skill directory or user-skills overlay, returning text or base64 data.

    Returns: {
      "encoding": "text" | "base64",
      "data": str,
      "mime_type": str | None,
      "truncated": bool
    }
    """
    # Check if this is a user-skills path
    if rel_path.startswith(f"user-skills/{name}/"):
        user_skills_dir = _resolve_user_skills_dir()
        user_skill_dir = user_skills_dir / name
        # Remove the "user-skills/{name}/" prefix
        user_rel_path = rel_path[len(f"user-skills/{name}/") :]
        file_path = (user_skill_dir / user_rel_path).resolve()

        # Prevent path traversal
        if user_skill_dir not in file_path.parents and file_path != user_skill_dir:
            raise ValueError("path must be within the user-skills directory")
    else:
        # Regular skill asset
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


def _resolve_user_skills_dir() -> Path:
    """
    function_purpose: Resolve user-skills directory from environment or default location.

    This directory contains user-created notes and assets that overlay Anthropic skills.
    """
    env_dir = os.environ.get("USER_SKILLS_DIR")
    return Path(env_dir).resolve() if env_dir else DEFAULT_USER_SKILLS_DIR


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
        "- skill_server_info(): server name, description, skills_dir, transport\n"
        "- skill_list_all(markdown_output?): brief skill metadata (supports markdown output)\n"
        "- skill_get_detail(name, include_notes?, markdown_output?): full skill with notes (supports markdown output)\n"
        "- skill_search_index(query, markdown_output?): substring search across skills (supports markdown output)\n"
        "- skill_list_assets(name, markdown_output?): non-SKILL.md files inside a skill (supports markdown output)\n"
        "- skill_list_notes(name, markdown_output?): list notes for a skill (supports markdown output)\n"
        "- skill_read_asset(name, path, max_bytes): read an asset within a skill (text/base64 + mime_type + truncated)\n"
        "- skill_create(name, description, body?, license?, allowed_tools?, metadata?): create a new skill directory with SKILL.md\n"
        "- skill_add_asset(name, path, content, encoding?, overwrite?): add a single asset file (text or base64) inside a skill\n"
        "- skill_add_assets(name, assets, overwrite?): bulk add multiple asset files to a skill\n"
        "- skill_store_note(name, title, content): append a note to a skill\n"
        "- skill_trash_user_skill(name, force?): move user-created skill to trash\n"
        "- skill_trash_user_asset(name, path): move user-created asset/note to trash\n"
        "\n"
        "Markdown Output:\n"
        "- Many tools support optional 'markdown_output=True' parameter for readable formatted output.\n"
        "- When enabled, returns formatted markdown string instead of JSON structure.\n"
        "- Useful for better readability and token efficiency when presenting information to LLMs.\n"
        "\n"
        "Notes:\n"
        "- Skills must adhere to Agent Skills Spec (SKILL.md with YAML frontmatter: name, description).\n"
        "- Immediate directory name must match 'name' in frontmatter (e.g., document-skills/docx with name: docx).\n"
        "- Invalid SKILL.md entries are surfaced with error diagnostics in metadata but do not stop discovery.\n"
        "\n"
        "Notes and Assets:\n"
        "- skill_get_detail() includes notes by default; notes contain corrections, improvements, and asset documentation.\n"
        "- Notes are appended to the skill body to provide complete context including learnings and examples.\n"
        "- IMPORTANT: When adding assets via skill_add_asset/skill_add_assets, ALWAYS create a note documenting:\n"
        "  * What the asset contains and its purpose\n"
        "  * When and why an agent should load/use it\n"
        "  * Any context or prerequisites needed to understand it\n"
        "  * Example usage patterns if applicable\n"
        "- This documentation practice ensures assets remain discoverable and usable over time.\n"
    ),
)


@mcp.tool
def skill_server_info() -> dict[str, Any]:
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
def skill_list_all(markdown_output: bool = False) -> list[dict[str, Any]] | str:
    """
    function_purpose: List available skills with brief metadata (excluding body).

    Description:
    - Enumerates all discovered skills from the skills directory and returns summary metadata.
    - Excludes the markdown body for compact listing; use get_skill_detail for full content.

    Args:
    - markdown_output: bool   If True, return formatted markdown string instead of JSON list (default: False)

    Returns:
    - If markdown_output=False: List of dict entries with name, description, license, allowed_tools, metadata, path
    - If markdown_output=True: formatted markdown string with skill catalog

    Usage:
    - Use this to present a catalog of available skills to the agent or user.
    - Set markdown_output=True for a more readable format.
    """
    skills_dir = _resolve_skills_dir()
    skills = discover_skills(skills_dir)
    skill_list = [
        {
            k: v
            for k, v in s.items()
            if k
            in {"name", "description", "license", "allowed_tools", "metadata", "path"}
        }
        for s in skills
    ]

    if not markdown_output:
        return skill_list

    # Format as markdown
    lines = ["# Available Skills\n\n"]
    for skill in skill_list:
        lines.append(f"## {skill['name']}\n")
        lines.append(f"{skill['description']}\n\n")
        if skill.get("license"):
            lines.append(f"**License:** {skill['license']}  \n")
        if skill.get("allowed_tools"):
            lines.append(f"**Allowed Tools:** {', '.join(skill['allowed_tools'])}  \n")
        if skill.get("path"):
            lines.append(f"**Path:** `{skill['path']}`  \n")
        lines.append("\n")

    return "".join(lines)


@mcp.tool
def skill_get_detail(
    name: str, include_notes: bool = True, markdown_output: bool = False
) -> dict[str, Any] | str:
    """
    function_purpose: Get full parsed details for a specific skill by name (frontmatter + body + notes).

    Description:
    - Returns the complete parsed skill including frontmatter fields and the markdown body content.
    - By default, appends all notes from the _notes/ directory to provide complete context including
      learnings, improvements, corrections, and examples discovered while using the skill.

    Args:
    - name: str               The hyphen-case name of the skill (must match the skill directory name)
    - include_notes: bool     If True (default), append notes from _notes/ to the body for complete context
    - markdown_output: bool   If True, return formatted markdown string instead of JSON dict (default: False)

    Returns:
    - If markdown_output=False: dict containing name, description, license?, allowed_tools?, metadata?, path, body
    - If markdown_output=True: formatted markdown string with frontmatter and body

    Usage:
    - Use this when the agent needs the full guidance text and metadata for a skill.
    - Notes are included by default to ensure the agent sees all relevant context, corrections, and examples.
    - Set include_notes=False only if you want just the core SKILL.md content without historical notes.
    - Set markdown_output=True to get a readable markdown document instead of JSON structure.
    """
    skills_dir = _resolve_skills_dir()
    skill = get_skill(skills_dir, name, include_notes=include_notes)

    if not markdown_output:
        return skill

    # Format as markdown
    lines = [f"# {skill['name']}\n"]
    lines.append(f"**Description:** {skill['description']}\n")

    if skill.get("license"):
        lines.append(f"**License:** {skill['license']}\n")

    if skill.get("allowed_tools"):
        lines.append(f"**Allowed Tools:** {', '.join(skill['allowed_tools'])}\n")

    if skill.get("metadata"):
        lines.append(f"**Metadata:** {skill['metadata']}\n")

    lines.append(f"**Path:** {skill['path']}\n")
    lines.append("\n---\n")
    lines.append(skill.get("body", ""))

    return "".join(lines)


@mcp.tool
def skill_search_index(
    query: str, markdown_output: bool = False
) -> list[dict[str, Any]] | str:
    """
    function_purpose: Search skills by case-insensitive substring across name, description, and body.

    Description:
    - Performs a simple substring search across the parsed name, description, and body for each skill.

    Args:
    - query: str              Case-insensitive substring
    - markdown_output: bool   If True, return formatted markdown string instead of JSON list (default: False)

    Returns:
    - If markdown_output=False: List of dicts with name, description, path
    - If markdown_output=True: formatted markdown string with search results

    Usage:
    - Use this to quickly locate relevant skills by topic or keywords.
    - Set markdown_output=True for a more readable format.
    """
    skills_dir = _resolve_skills_dir()
    results = search_skills(skills_dir, query)

    if not markdown_output:
        return results

    # Format as markdown
    if not results:
        return f"# Search Results for '{query}'\n\nNo matches found.\n"

    lines = [f"# Search Results for '{query}'\n\n"]
    lines.append(f"Found {len(results)} match(es):\n\n")
    for skill in results:
        lines.append(f"## {skill['name']}\n")
        lines.append(f"{skill['description']}\n\n")
        if skill.get("path"):
            lines.append(f"**Path:** `{skill['path']}`\n\n")

    return "".join(lines)


@mcp.tool
def skill_list_assets(
    name: str, markdown_output: bool = False
) -> list[dict[str, Any]] | str:
    """
    function_purpose: List non-SKILL.md files within a skill folder (recursive).

    Description:
    - Enumerates files inside a specific skill directory, excluding SKILL.md, recursively.
    - Useful for discovering supporting artifacts, reference materials, templates, and helper scripts that belong to a skill.

    Args:
    - name: str               The hyphen-case name of the skill whose assets to list
    - markdown_output: bool   If True, return formatted markdown string instead of JSON list (default: False)

    Returns:
    - If markdown_output=False: list of dicts with path, size, mime_type
    - If markdown_output=True: formatted markdown string with asset listing

    Usage:
    - Call before reading assets to present available files to the agent or user.
    - For reading actual content, use skill_read_asset() with the returned path.
    - Set markdown_output=True for a more readable format.
    """
    skills_dir = _resolve_skills_dir()
    assets = list_skill_assets(skills_dir, name)

    if not markdown_output:
        return assets

    # Format as markdown
    if not assets:
        return f"# Assets for '{name}'\n\nNo assets found.\n"

    lines = [f"# Assets for '{name}'\n\n"]
    lines.append(f"Found {len(assets)} asset(s):\n\n")
    lines.append("| Path | Size | MIME Type |\n")
    lines.append("|------|------|----------|\n")
    for asset in assets:
        path = asset.get("path", "")
        size = asset.get("size")
        size_str = f"{size:,} bytes" if size is not None else "N/A"
        mime = asset.get("mime_type") or "unknown"
        lines.append(f"| `{path}` | {size_str} | {mime} |\n")

    return "".join(lines)


@mcp.tool
def skill_read_asset(
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
def skill_create(
    name: str,
    description: str,
    body: str = "",
    license: str | None = None,
    allowed_tools: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    function_purpose: Create a new skill directory containing a SKILL.md per Agent Skills Spec.

    Description:
    - Creates a new directory under the skills root whose name matches the skill 'name' frontmatter.
    - Writes a SKILL.md file with YAML frontmatter (name, description, optional license, allowed_tools, metadata)
      followed by the markdown body.
    - Fails if a skill with that name already exists or if the name is invalid.

    Constraints:
    - Additive only; will not overwrite existing skills.
    - Name must be hyphen-case or simple alphanumeric with dashes/underscores.
    - Body may be empty; if empty a placeholder is inserted.

    Args:
    - name: str                 Skill directory and frontmatter name (hyphen-case recommended)
    - description: str          Concise description of the skill
    - body: str                 Markdown guidance content (optional)
    - license: str | None       Optional license identifier/text
    - allowed_tools: list[str]  Optional list of tool names this skill permits
    - metadata: dict[str, Any]  Optional arbitrary metadata mapping

    Returns:
    - dict[str, Any] with:
      - created: bool
      - path: str (relative path to SKILL.md within skills dir)
      - message: str status narrative
    """
    # Basic validation of name
    if not name or any(ch for ch in name if not (ch.isalnum() or ch in "-_")):
        return {
            "created": False,
            "path": "",
            "message": "Invalid skill name characters",
        }
    if name.startswith("-") or name.endswith("-"):
        return {
            "created": False,
            "path": "",
            "message": "Skill name cannot start/end with dash",
        }

    skills_dir = _resolve_skills_dir()
    sdir = skills_dir / name
    if sdir.exists():
        return {"created": False, "path": "", "message": "Skill already exists"}

    try:
        sdir.mkdir(parents=True, exist_ok=False)
    except Exception as exc:
        return {
            "created": False,
            "path": "",
            "message": f"Failed to create directory: {exc}",
        }

    # Prepare frontmatter lines
    fm_lines = ["---", f'name: "{name}"', f'description: "{description}"']
    if license:
        fm_lines.append(f'license: "{license}"')
    if allowed_tools:
        # Simple YAML list
        fm_lines.append("allowed_tools:")
        for t in allowed_tools:
            fm_lines.append(f"  - {t}")
    if metadata:
        fm_lines.append("metadata:")
        for k, v in metadata.items():
            fm_lines.append(
                f"  {k}: {v!r}".replace("\\'", "'")
            )  # crude repr -> YAML-ish
    fm_lines.append("---")
    fm = "\n".join(fm_lines)

    content_body = body.strip()
    if not content_body:
        content_body = f"# {name}\n\n{description}\n\n(Placeholder body – update with detailed guidance.)"
    skill_md = fm + "\n\n" + content_body.rstrip() + "\n"

    skill_path = sdir / "SKILL.md"
    try:
        with open(skill_path, "x", encoding="utf-8") as f:
            _ = f.write(skill_md)
    except Exception as exc:
        return {
            "created": False,
            "path": "",
            "message": f"Failed to write SKILL.md: {exc}",
        }

    rel = skill_path.relative_to(skills_dir).as_posix()
    return {"created": True, "path": rel, "message": "Skill created"}


def _add_skill_asset_impl(
    name: str,
    path: str,
    content: str,
    encoding: str = "text",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Internal implementation for adding skill assets."""
    skills_dir = _resolve_skills_dir()
    skill_root = skill_dir_for_name(skills_dir, name)

    if not path or path.startswith("/") or ".." in path:
        return {
            "written": False,
            "path": path,
            "size": None,
            "message": "Invalid path",
            "binary": encoding == "base64",
        }

    target = (skill_root / path).resolve()
    try:
        if not str(target).startswith(str(skill_root.resolve())):
            return {
                "written": False,
                "path": path,
                "size": None,
                "message": "Path traversal detected",
                "binary": encoding == "base64",
            }
    except Exception:
        return {
            "written": False,
            "path": path,
            "size": None,
            "message": "Path resolution failed",
            "binary": encoding == "base64",
        }

    if target.exists() and not overwrite:
        return {
            "written": False,
            "path": path,
            "size": None,
            "message": "File exists (set overwrite=True to replace)",
            "binary": encoding == "base64",
        }

    target.parent.mkdir(parents=True, exist_ok=True)

    binary = encoding == "base64"
    try:
        if binary:
            import base64

            raw = base64.b64decode(content)
            with open(target, "wb") as f:
                f.write(raw)
            size = len(raw)
        else:
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            size = target.stat().st_size
    except Exception as exc:
        return {
            "written": False,
            "path": path,
            "size": None,
            "message": f"Write failed: {exc}",
            "binary": binary,
        }

    return {
        "written": True,
        "path": str(target.relative_to(skill_root)),
        "size": size,
        "message": "Asset written",
        "binary": binary,
    }


@mcp.tool
def skill_add_asset(
    name: str,
    path: str,
    content: str,
    encoding: str = "text",
    overwrite: bool = False,
) -> dict[str, Any]:
    """
    function_purpose: Add (or optionally overwrite) a single asset file inside an existing skill directory.

    Description:
    - Writes a new file under the skill folder (creating parent directories) while enforcing path safety.
    - Supports text (UTF-8) or base64 content for binary assets (e.g. PDFs, images).
    - Will not overwrite existing files unless overwrite=True.

    IMPORTANT: After adding an asset, you should ALWAYS create a note (via skill_store_note) documenting:
    - What the asset contains and its purpose
    - When and why an agent should load/use it
    - Any context needed to understand it
    - Example usage patterns if applicable

    This ensures the asset remains discoverable and properly documented for future use.

    Args:
    - name: str          Skill name (directory must already exist)
    - path: str          Relative path inside the skill (e.g. "examples/foo.py")
    - content: str       Text content or base64 string
    - encoding: str      "text" (default) or "base64"
    - overwrite: bool    Allow overwriting when True (default False)

    Returns:
    - dict with:
      - written: bool
      - path: str            Relative normalized path
      - size: int | None
      - message: str
      - binary: bool
    """
    return _add_skill_asset_impl(name, path, content, encoding, overwrite)


@mcp.tool
def skill_add_assets(
    name: str,
    assets: list[dict[str, Any]],
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """
    function_purpose: Bulk add multiple assets to a skill.

    Description:
    - Convenience wrapper over add_skill_asset for efficiency when scaffolding several files.
    - Applies a shared overwrite policy (individual entries may still be rejected if invalid).

    IMPORTANT: After adding assets, you should ALWAYS create a note (via skill_store_note) documenting:
    - What each asset contains and its purpose
    - When and why an agent should load/use them
    - Any context needed to understand them
    - Example usage patterns if applicable

    Args:
    - name: str                   Skill name
    - assets: list[dict]          Each: {path: str, content: str, encoding?: "text"|"base64"}
    - overwrite: bool             Allow overwriting existing files

    Returns:
    - list of result dicts (see add_skill_asset).
    """
    results: list[dict[str, Any]] = []
    for entry in assets:
        p = entry.get("path")
        c = entry.get("content", "")
        enc = entry.get("encoding", "text")
        if not isinstance(p, str) or not isinstance(c, str):
            results.append(
                {
                    "written": False,
                    "path": p or "",
                    "size": None,
                    "message": "Invalid asset entry",
                    "binary": enc == "base64",
                }
            )
            continue
        results.append(_add_skill_asset_impl(name, p, c, enc, overwrite))
    return results


@mcp.tool
def skill_store_note(name: str, title: str, content: str) -> dict[str, Any]:
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
            _ = f.write(body)
        rel = note_path.relative_to(sdir).as_posix()
        return {"path": rel, "created": True, "message": "Note stored"}
    except FileExistsError:
        # Extremely unlikely due to timestamp; retry with suffix
        alt = notes_dir / f"{ts}-{slug}-1.md"
        with open(alt, "x", encoding="utf-8") as f:
            _ = f.write(body)
        rel = alt.relative_to(sdir).as_posix()
        return {"path": rel, "created": True, "message": "Note stored (with suffix)"}
    except Exception as exc:
        return {"path": "", "created": False, "message": f"Failed to store note: {exc}"}


@mcp.tool
def skill_list_notes(
    name: str, markdown_output: bool = False
) -> list[dict[str, Any]] | str:
    """
    function_purpose: List notes created under a skill's _notes directory.

    Description:
    - Enumerates note files stored under a skill's '_notes' and 'notes' directories. Notes are additive records of
      learnings, improvements, and scripts created via store_skill_note() or manually, intended to refine or clarify
      skills over time without editing existing files.

    Args:
    - name: str               The hyphen-case skill name (must match the skill directory)
    - markdown_output: bool   If True, return formatted markdown string instead of JSON list (default: False)

    Returns:
    - If markdown_output=False: list of dicts with path, size, title, created_at, kind
    - If markdown_output=True: formatted markdown string with note listing

    Usage:
    - Use this to browse available notes and select one to read with skill_read_asset().
    - Set markdown_output=True for a more readable format.
    """
    skills_dir = _resolve_skills_dir()
    user_skills_dir = _resolve_user_skills_dir()
    sdir = skill_dir_for_name(skills_dir, name)
    results: list[dict[str, Any]] = []

    # Check both skill's own notes and user-skills overlay
    found_any = False

    # Check skill's own _notes/ and notes/ directories
    for notes_dirname in ["_notes", "notes"]:
        notes_dir = sdir / notes_dirname
        if not notes_dir.exists():
            continue
        found_any = True

        for f in notes_dir.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(sdir).as_posix()
            try:
                size = f.stat().st_size
            except OSError:
                size = None

            title: str | None = None
            created_at: str | None = None
            kind: str | None = None

            # Best-effort parse of YAML frontmatter if present
            try:
                txt = f.read_text(encoding="utf-8")
                lines = txt.splitlines(keepends=False)
                if lines and lines[0].strip() == "---":
                    fm_lines: list[str] = []
                    idx = 1
                    while idx < len(lines) and lines[idx].strip() != "---":
                        fm_lines.append(lines[idx])
                        idx += 1
                    if idx < len(lines) and lines[idx].strip() == "---":
                        fm = yaml.safe_load("\n".join(fm_lines)) or {}
                        if isinstance(fm, dict):
                            t = fm.get("title")
                            ca = fm.get("created_at")
                            k = fm.get("kind")
                            title = t if isinstance(t, str) else None
                            created_at = ca if isinstance(ca, str) else None
                            kind = k if isinstance(k, str) else None
            except Exception:
                # Ignore parsing errors; still include the file in results
                pass

            results.append(
                {
                    "path": rel,
                    "size": size,
                    "title": title,
                    "created_at": created_at,
                    "kind": kind,
                }
            )

    # Check user-skills overlay directory
    user_skill_dir = user_skills_dir / name
    if user_skill_dir.exists():
        for notes_dirname in ["_notes", "notes"]:
            user_notes_dir = user_skill_dir / notes_dirname
            if not user_notes_dir.exists():
                continue
            found_any = True

            for f in user_notes_dir.rglob("*"):
                if not f.is_file():
                    continue
                try:
                    rel_path = (
                        f"user-skills/{name}/"
                        + f.relative_to(user_skill_dir).as_posix()
                    )
                except ValueError:
                    rel_path = f.name
                try:
                    size = f.stat().st_size
                except OSError:
                    size = None

                title: str | None = None
                created_at: str | None = None
                kind: str | None = None

                # Best-effort parse of YAML frontmatter if present
                try:
                    txt = f.read_text(encoding="utf-8")
                    lines = txt.splitlines(keepends=False)
                    if lines and lines[0].strip() == "---":
                        fm_lines: list[str] = []
                        idx = 1
                        while idx < len(lines) and lines[idx].strip() != "---":
                            fm_lines.append(lines[idx])
                            idx += 1
                        if idx < len(lines) and lines[idx].strip() == "---":
                            fm = yaml.safe_load("\n".join(fm_lines)) or {}
                            if isinstance(fm, dict):
                                t = fm.get("title")
                                ca = fm.get("created_at")
                                k = fm.get("kind")
                                title = t if isinstance(t, str) else None
                                created_at = ca if isinstance(ca, str) else None
                                kind = k if isinstance(k, str) else None
                except Exception:
                    # Ignore parsing errors; still include the file in results
                    pass

                results.append(
                    {
                        "path": rel_path,
                        "size": size,
                        "title": title,
                        "created_at": created_at,
                        "kind": kind,
                    }
                )

    if not found_any:
        if markdown_output:
            return f"# Notes for '{name}'\n\nNo notes directory found.\n"
        return results

    # Stable ordering by path
    results.sort(key=lambda x: x.get("path") or "")

    if not markdown_output:
        return results

    # Format as markdown
    if not results:
        return f"# Notes for '{name}'\n\nNo notes found.\n"

    lines = [f"# Notes for '{name}'\n\n"]
    lines.append(f"Found {len(results)} note(s):\n\n")
    lines.append("| Title | Created | Path | Size |\n")
    lines.append("|-------|---------|------|------|\n")
    for note in results:
        title = note.get("title") or "Untitled"
        created = note.get("created_at") or "N/A"
        path = note.get("path", "")
        size = note.get("size")
        size_str = f"{size:,} bytes" if size is not None else "N/A"
        lines.append(f"| {title} | {created} | `{path}` | {size_str} |\n")

    return "".join(lines)


@mcp.tool
def skill_trash_user_skill(name: str, force: bool = True) -> dict[str, Any]:
    """
    function_purpose: Move a user-created skill directory into a trash location instead of hard deleting it.

    Policy:
    - Only user-created skills may be trashed. Bundled/Anthropic skills are rejected.
    - The skill directory is moved under a trash/skills subdirectory with a timestamped folder name.
    - All operations are logged to an operations log file.

    Args:
    - name: str   Skill name to trash
    - force: bool Require explicit confirmation flag (default True). If False, the call is a dry refusal.

    Returns:
    - dict[str, Any] with:
      - trashed: bool
      - name: str
      - trash_path: str | None
      - message: str
    """
    from datetime import datetime, timezone
    import shutil

    skills_dir = _resolve_skills_dir()
    trash_dir = _resolve_trash_dir()

    # Anthropic/bundled skills live under the primary skills_dir and must not be trashed.
    try:
        sdir = skill_dir_for_name(skills_dir, name)
    except Exception:
        return {
            "trashed": False,
            "name": name,
            "trash_path": None,
            "message": "Skill not found",
        }

    # If this is an Anthropic/bundled skill, refuse. User-created skills should be placed in a separate
    # directory tree by configuration if stronger separation is needed.
    if _is_anthropic_skill(skills_dir, name):
        _log_operation(
            "skill_trash_user_skill_denied",
            {"skill": name, "reason": "anthropic_skill"},
        )
        return {
            "trashed": False,
            "name": name,
            "trash_path": None,
            "message": "Trashing bundled/Anthropic skills is not allowed",
        }

    if not force:
        return {
            "trashed": False,
            "name": name,
            "trash_path": None,
            "message": "Set force=True to move skill to trash",
        }

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trash_root = trash_dir / "skills"
    trash_root.mkdir(parents=True, exist_ok=True)
    trash_target = trash_root / f"{ts}__{name}"

    try:
        shutil.move(str(sdir), str(trash_target))
    except Exception as exc:
        _log_operation(
            "skill_trash_user_skill_error",
            {"skill": name, "error": str(exc)},
        )
        return {
            "trashed": False,
            "name": name,
            "trash_path": None,
            "message": f"Failed to move skill to trash: {exc}",
        }

    rel_trash = trash_target.as_posix()
    _log_operation(
        "skill_trash_user_skill",
        {"skill": name, "trash_path": rel_trash},
    )
    return {
        "trashed": True,
        "name": name,
        "trash_path": rel_trash,
        "message": "Skill moved to trash",
    }


@mcp.tool
def skill_trash_user_asset(name: str, path: str) -> dict[str, Any]:
    """
    function_purpose: Move a user-created asset or note into trash instead of deleting it.

    Policy:
    - For bundled/Anthropic skills:
      * Only assets under reserved user areas are allowed:
        - "_user_assets/" subtree
        - "_user_notes/" subtree
        - "_notes/" subtree (programmatic notes from skill_store_note)
        - "notes/" subtree (manually created notes)
      * Core assets (including SKILL.md and any other non-user files) cannot be trashed.
    - For user-created skills:
      * Any asset path under the skill directory may be trashed.
    - The target file is moved under trash/assets/<skill_name>/<timestamp>__<relative_path>.
    - Operations are logged in an operations log.

    Args:
    - name: str   Skill name
    - path: str   Relative path within the skill directory

    Returns:
    - dict[str, Any] with:
      - trashed: bool
      - name: str
      - path: str
      - trash_path: str | None
      - message: str
    """
    from datetime import datetime, timezone

    skills_dir = _resolve_skills_dir()
    trash_dir = _resolve_trash_dir()

    try:
        skill_root = skill_dir_for_name(skills_dir, name)
    except Exception:
        return {
            "trashed": False,
            "name": name,
            "path": path,
            "trash_path": None,
            "message": "Skill not found",
        }

    if not path or path.startswith("/"):
        return {
            "trashed": False,
            "name": name,
            "path": path,
            "trash_path": None,
            "message": "Invalid asset path",
        }

    target = (skill_root / path).resolve()
    try:
        if (
            skill_root.resolve() not in target.parents
            and target != skill_root.resolve()
        ):
            return {
                "trashed": False,
                "name": name,
                "path": path,
                "trash_path": None,
                "message": "Path traversal detected",
            }
    except Exception:
        return {
            "trashed": False,
            "name": name,
            "path": path,
            "trash_path": None,
            "message": "Path resolution failed",
        }

    if not target.exists() or not target.is_file():
        return {
            "trashed": False,
            "name": name,
            "path": path,
            "trash_path": None,
            "message": "Asset does not exist",
        }

    # Enforce Anthropic vs user-skill policy.
    is_anthropic = _is_anthropic_skill(skills_dir, name)
    rel_from_root = target.relative_to(skill_root).as_posix()

    if is_anthropic:
        # Only user-reserved subtrees allowed for Anthropic skills.
        # Accept both _notes/ (programmatic), notes/ (manual), _user_notes/ (new convention), and _user_assets/
        if not (
            rel_from_root.startswith("_user_assets/")
            or rel_from_root.startswith("_user_notes/")
            or rel_from_root.startswith("_notes/")
            or rel_from_root.startswith("notes/")
        ):
            _log_operation(
                "skill_trash_user_asset_denied",
                {
                    "skill": name,
                    "path": rel_from_root,
                    "reason": "anthropic_core_asset",
                },
            )
            return {
                "trashed": False,
                "name": name,
                "path": path,
                "trash_path": None,
                "message": "Only user-created assets/notes under _user_assets/, _user_notes/, _notes/, or notes/ may be trashed for Anthropic skills",
            }
        if rel_from_root == "SKILL.md":
            _log_operation(
                "skill_trash_user_asset_denied",
                {
                    "skill": name,
                    "path": rel_from_root,
                    "reason": "anthropic_skill_md",
                },
            )
            return {
                "trashed": False,
                "name": name,
                "path": path,
                "trash_path": None,
                "message": "Cannot trash SKILL.md for Anthropic skills",
            }

    # Compute trash target path.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trash_root = trash_dir / "assets" / name
    trash_root.mkdir(parents=True, exist_ok=True)
    safe_rel = rel_from_root.replace("/", "__")
    trash_target = trash_root / f"{ts}__{safe_rel}"

    try:
        import shutil

        shutil.move(str(target), str(trash_target))
    except Exception as exc:
        _log_operation(
            "skill_trash_user_asset_error",
            {
                "skill": name,
                "path": rel_from_root,
                "error": str(exc),
            },
        )
        return {
            "trashed": False,
            "name": name,
            "path": path,
            "trash_path": None,
            "message": f"Failed to move asset to trash: {exc}",
        }

    # Best-effort cleanup of empty parent directories under the skill root.
    parent = target.parent
    try:
        skill_root_resolved = skill_root.resolve()
        while parent != skill_root_resolved and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    except Exception:
        # Do not fail the operation due to cleanup errors.
        pass

    rel_trash = trash_target.as_posix()
    _log_operation(
        "skill_trash_user_asset",
        {
            "skill": name,
            "path": rel_from_root,
            "trash_path": rel_trash,
        },
    )
    return {
        "trashed": True,
        "name": name,
        "path": path,
        "trash_path": rel_trash,
        "message": "Asset moved to trash",
    }


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
