"""Read-only, on-demand method notes, independent of executable plugins."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harness.plugin import NAME_RE


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    plugin: str | None = None


def discover(root: Path, plugins: dict) -> dict[str, Skill]:
    """Resolve skills by filename; plugin packages may ship one beside tools."""
    paths = [(path, None) for path in sorted((root / "skills").glob("*.md"))]
    paths += [(Path(plugin.skill) if plugin.skill else root / "plugins" / name / "SKILL.md", name)
              for name, plugin in plugins.items()]
    found: dict[str, Skill] = {}
    for path, plugin in paths:
        if not path.is_file():
            continue
        name = plugin or path.stem
        if not NAME_RE.fullmatch(name) or name in found:
            raise ValueError(f"invalid or duplicate skill name: {name}")
        body = path.read_text(encoding="utf-8").strip()
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if len(lines) < 2 or not lines[0].startswith("# ") or not lines[1].startswith("> "):
            raise ValueError(f"skill {name} needs a title and one-line description")
        description = lines[1][2:].strip()
        if not description or len(description) > 300 or len(body) > 4000:
            raise ValueError(f"skill {name} has an invalid description or body size")
        found[name] = Skill(name, description, body, plugin)
    return found
