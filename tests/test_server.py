from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from skills_mcp.server import (
    discover_skills,
    get_skill,
    list_skill_assets,
    read_skill_asset,
)


def _skills_dir() -> Path:
    # tests/ -> project root is parent, skills under project root
    root = Path(__file__).resolve().parents[1]
    return root / "skills"


def test_discover_skills_basic() -> None:
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        pytest.skip(f"skills directory not found at {skills_dir}")

    skills = discover_skills(skills_dir)
    assert isinstance(skills, list)
    assert len(skills) > 0, "No skills discovered"

    for s in skills:
        # minimal shape checks
        assert "name" in s and isinstance(s["name"], str) and s["name"], (
            "skill missing name"
        )
        assert "description" in s and isinstance(s["description"], str), (
            "skill missing description"
        )
        assert "path" in s and isinstance(s["path"], str), "skill missing path"
        # path should point to SKILL.md relative to skills dir
        assert s["path"].endswith("SKILL.md"), (
            f"path should end with SKILL.md: {s['path']}"
        )


def test_get_skill_detail_matches_discovery() -> None:
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        pytest.skip(f"skills directory not found at {skills_dir}")

    skills = discover_skills(skills_dir)
    assert skills, "No skills discovered"

    # Use the first valid skill entry
    first = skills[0]
    name = first["name"]
    detail = get_skill(skills_dir, name)

    # Should contain body and match basic frontmatter
    assert isinstance(detail, dict)
    assert detail["name"] == name
    assert "body" in detail and isinstance(detail["body"], str)


def _find_skill_with_assets(
    skills_dir: Path, candidate_names: list[str]
) -> tuple[str, list[dict[str, Any]]]:
    """
    Attempt to find a skill that has non-SKILL.md assets.
    Returns (skill_name, assets list). Raises RuntimeError if none found.
    """
    # Try specific known candidates first, then fall back to any discovered skill
    tried: set[str] = set()

    for nm in candidate_names:
        try:
            assets = list_skill_assets(skills_dir, nm)
            tried.add(nm)
            if assets:
                return nm, assets
        except Exception:
            # ignore invalid names
            pass

    # Fallback: iterate all discovered skills
    for s in discover_skills(skills_dir):
        nm = s["name"]
        if nm in tried:
            continue
        try:
            assets = list_skill_assets(skills_dir, nm)
            if assets:
                return nm, assets
        except Exception:
            continue

    raise RuntimeError("No skill with assets found")


def test_list_skill_assets_excludes_skill_md_and_has_shape() -> None:
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        pytest.skip(f"skills directory not found at {skills_dir}")

    # Likely to have assets in document-skills
    candidate_names = [
        "docx",
        "pdf",
        "pptx",
        "xlsx",
        "algorithmic-art",
        "brand-guidelines",
        "template-skill",
    ]
    try:
        name, assets = _find_skill_with_assets(skills_dir, candidate_names)
    except RuntimeError:
        pytest.skip("Could not find any skill with assets to validate")

    assert isinstance(assets, list)
    assert len(assets) > 0

    for a in assets:
        assert "path" in a and isinstance(a["path"], str), "asset missing path"
        assert "size" in a, "asset missing size"
        assert "mime_type" in a, "asset missing mime_type"
        # Ensure SKILL.md not included
        assert a["path"] != "SKILL.md", "SKILL.md should not be listed as an asset"


def test_read_skill_asset_returns_text_for_markdown_when_available() -> None:
    skills_dir = _skills_dir()
    if not skills_dir.exists():
        pytest.skip(f"skills directory not found at {skills_dir}")

    candidate_names = [
        "docx",
        "pdf",
        "pptx",
        "xlsx",
        "algorithmic-art",
        "brand-guidelines",
        "template-skill",
    ]

    try:
        name, assets = _find_skill_with_assets(skills_dir, candidate_names)
    except RuntimeError:
        pytest.skip("Could not find any skill with assets to read")

    # Prefer a markdown/text asset if present
    md_asset = None
    for a in assets:
        if a.get("mime_type") in ("text/markdown", "text/plain") or a["path"].endswith(
            (".md", ".txt")
        ):
            md_asset = a
            break

    if md_asset is None:
        pytest.skip("No markdown/plain text asset found to validate read_skill_asset")

    payload = read_skill_asset(skills_dir, name, md_asset["path"], max_bytes=256 * 1024)
    assert isinstance(payload, dict)
    assert payload.get("encoding") == "text"
    assert isinstance(payload.get("data"), str)
    # Ensure non-empty text
    assert payload["data"].strip(), "read text data should not be empty"
    # If MIME was known, it should match expectation
    if md_asset.get("mime_type"):
        assert payload["mime_type"] == md_asset["mime_type"]
