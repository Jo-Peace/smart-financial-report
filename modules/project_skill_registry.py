"""Local project skill registry.

This is intentionally small: it gives the Python pipeline a structured way to
discover the Markdown playbooks in project_skills without adopting a full agent
OS or dashboard.
"""

from __future__ import annotations

import json
from pathlib import Path


DEFAULT_REGISTRY_PATH = Path("project_skills/registry.json")


def load_skill_registry(base_dir: str | Path = ".", registry_path: str | Path = DEFAULT_REGISTRY_PATH) -> dict:
    """Load and validate the project skill registry."""
    base = Path(base_dir)
    path = base / registry_path
    data = json.loads(path.read_text(encoding="utf-8"))
    skills = data.get("skills", [])
    if not isinstance(skills, list):
        raise ValueError("project skill registry must contain a skills array")

    for skill in skills:
        skill_path = base / skill.get("path", "")
        skill["exists"] = skill_path.exists()
        if not skill.get("id"):
            raise ValueError("project skill is missing id")
        if not skill["exists"]:
            raise FileNotFoundError(f"project skill file not found: {skill_path}")

    data["skills"] = sorted(skills, key=lambda item: item.get("priority", 999))
    return data


def select_skills(context_tags, registry: dict | None = None, base_dir: str | Path = ".") -> list[dict]:
    """Return skills matching any context tag, ordered by priority."""
    registry = registry or load_skill_registry(base_dir=base_dir)
    wanted = {str(tag) for tag in context_tags}
    selected = []
    for skill in registry.get("skills", []):
        tags = set(skill.get("tags", [])) | set(skill.get("applies_to", []))
        if wanted & tags:
            selected.append(skill)
    return selected


def format_skill_summary(skills: list[dict]) -> str:
    """Human-readable one-line summary for logs or reports."""
    if not skills:
        return "No project skills selected."
    return ", ".join(f"{skill['id']}:{skill.get('priority', 999)}" for skill in skills)


def format_skill_context(skills: list[dict]) -> str:
    """Concise prompt-ready guidance for selected skills."""
    if not skills:
        return ""

    lines = ["## Project skill guidance"]
    for skill in skills:
        trigger = skill.get("trigger", "").strip()
        contract = skill.get("output_contract", "").strip()
        parts = [part for part in [trigger, contract] if part]
        detail = " | ".join(parts)
        lines.append(f"- {skill['id']}: {detail}")
    return "\n".join(lines)


def load_skill_text(skill: dict, base_dir: str | Path = ".") -> str:
    """Load a single skill Markdown body."""
    return (Path(base_dir) / skill["path"]).read_text(encoding="utf-8")
