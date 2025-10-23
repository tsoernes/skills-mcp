from __future__ import annotations

from pathlib import Path
from typing import Any

import base64
import logging
from mimetypes import guess_type

import yaml
from fastmcp import FastMCP

"""
FastMCP STDIO server exposing Anthropic Claude skills (Agent Skills Spec) as tools.

Dependencies (install with uv):
- fastmcp
- pyyaml

Suggested setup:
- uv add fastmcp pyyaml
- python skills-mcp/mcp_server.py   # runs as STDIO MCP server (default)

Exposed tools:
- list_skills() -> list[dict]: brief metadata for all skills
- get_skill_detail(name: str) -> dict: full parsed SKILL.md including body
- search_skill_index(query: str) -> list[dict]: brief matches for query
- list_skill_assets(name: str) -> list[dict]: non-SKILL.md files under the skill
- read_skill_asset(name: str, path: str, max_bytes: int = 1_048_576) -> dict: read a file

The server discovers skills by scanning the sibling "skills" directory for SKILL.md files
that follow the Agent Skills Spec (YAML frontmatter + Markdown body).
"""

# ---- Configuration ----
SERVER_NAME = "ClaudeSkills"
ROOT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = ROOT_DIR / "skills"

logger = logging.getLogger(SERVER_NAME)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


# ---- Helpers: Skill discovery and parsing ----
def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_frontmatter_and_body(text: str) -> tuple[dict[str, Any], str]:
    """
    Parse YAML frontmatter delimited by lines with only '---', followed by Markdown body.
    Returns (frontmatter_dict, body_text).
    """
    lines = text.splitlines(keepends=False)
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must begin with a '---' line for YAML frontmatter")

    # Find closing '---'
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


def parse_skill_md(md_path: Path) -> dict[str, Any]:
    """
    Parse a SKILL.md file and return structured data.

    Returns a dict containing:
    - name (str)
    - description (str)
    - license (str | None)
    - allowed_tools (list[str] | None)
    - metadata (dict[str, str] | None)
    - path (str) path relative to skills dir
    - body (str) markdown content
    """
    text = _read_text(md_path)
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

    # Validate directory name matches 'name' (Agent Skills Spec)
    dir_name = md_path.parent.name
    if dir_name != name:
        raise ValueError(
            f"skill directory '{dir_name}' must match frontmatter name '{name}'"
        )

    rel_path = str(md_path.relative_to(SKILLS_DIR))
    return {
        "name": name,
        "description": description,
        "license": license_ if isinstance(license_, str) else None,
        "allowed_tools": allowed_tools if isinstance(allowed_tools, list) else None,
        "metadata": metadata if isinstance(metadata, dict) else None,
        "path": rel_path,
        "body": body,
    }


def _iter_skill_md_paths() -> list[Path]:
    if not SKILLS_DIR.exists():
        logger.warning("Skills directory not found at %s", SKILLS_DIR)
        return []
    return list(SKILLS_DIR.rglob("SKILL.md"))


def discover_skills() -> list[dict[str, Any]]:
    """
    Find and parse all SKILL.md files under SKILLS_DIR.
    Returns list of skill dicts. Invalid skills are included with error metadata.
    """
    skills: list[dict[str, Any]] = []
    for md_path in _iter_skill_md_paths():
        try:
            data = parse_skill_md(md_path)
            skills.append(data)
        except Exception as exc:
            # Include diagnostics so agents can surface/fix issues
            rel_path = str(md_path.relative_to(SKILLS_DIR))
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
    # Stable ordering
    skills.sort(key=lambda s: (s.get("name") or "", s.get("path") or ""))
    return skills


def get_skill(name: str) -> dict[str, Any]:
    for skill in discover_skills():
        if skill.get("name") == name:
            return skill
    raise ValueError(f"skill '{name}' not found")


def search_skills(query: str) -> list[dict[str, Any]]:
    """
    Case-insensitive substring search across name, description, and body.
    Returns brief matches: {name, description, path}.
    """
    q = (query or "").strip().lower()
    results: list[dict[str, Any]] = []
    if not q:
        return results
    for s in discover_skills():
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


def _skill_dir(name: str) -> Path:
    # Find the SKILL.md for this name to determine its directory
    for md_path in _iter_skill_md_paths():
        try:
            data = parse_skill_md(md_path)
            if data.get("name") == name:
                return md_path.parent
        except Exception:
            # Skip invalids for resolving directory
            continue
    raise ValueError(f"skill '{name}' not found")


def _is_text_data(data: bytes, mime_type: str | None) -> bool:
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


def list_files_under(path: Path) -> list[Path]:
    return [p for p in path.rglob("*") if p.is_file()]


# ---- FastMCP server and tools ----
mcp = FastMCP(SERVER_NAME)


@mcp.tool
def list_skills() -> list[dict[str, Any]]:
    """
    List available Claude skills and their metadata.

    Returns: array of {name, description, license?, allowed_tools?, metadata?, path}
    """
    skills = discover_skills()
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
    Get full details (including Markdown body) of a specific skill by name.
    """
    return get_skill(name)


@mcp.tool
def search_skill_index(query: str) -> list[dict[str, Any]]:
    """
    Search skills by text query (name, description, body). Returns brief matches.
    """
    return search_skills(query)


@mcp.tool
def list_skill_assets(name: str) -> list[dict[str, Any]]:
    """
    List non-SKILL.md files within a skill directory.

    Returns: list of {path, size, mime_type}
    """
    sdir = _skill_dir(name)
    assets: list[dict[str, Any]] = []
    for f in list_files_under(sdir):
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


@mcp.tool
def read_skill_asset(
    name: str, path: str, max_bytes: int = 1_048_576
) -> dict[str, Any]:
    """
    Read a file inside the given skill directory.

    Args:
    - name: skill name (hyphen-case)
    - path: relative file path within the skill directory
    - max_bytes: maximum bytes to read (default 1 MiB)

    Returns: {
      "encoding": "text" | "base64",
      "data": str,
      "mime_type": str | None,
      "truncated": bool
    }
    """
    sdir = _skill_dir(name)
    file_path = (sdir / path).resolve()

    # Prevent path traversal
    if sdir not in file_path.parents and file_path != sdir:
        raise ValueError("path must be within the skill directory")

    if not file_path.exists() or not file_path.is_file():
        raise ValueError(f"file not found: {path}")

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


if __name__ == "__main__":
    # Run as STDIO server (default transport)
    mcp.run()
