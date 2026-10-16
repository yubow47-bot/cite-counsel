"""Read-time upgrades for saved JSON objects.

A saved version is immutable: its stored content and fingerprint never
change. Older schemas are upgraded only in the dict handed to the reader.
A schema with no registered path (including a future one) is rejected.
"""

from __future__ import annotations

from collections.abc import Callable


def upgrade(data, current: str, migrations: dict[str, Callable[[dict], dict]], label: str) -> dict:
    """Apply ``migrations[schema]`` step by step until ``data`` is ``current``."""
    seen = set()
    while isinstance(data, dict) and data.get("schema") != current:
        schema = data.get("schema")
        step = migrations.get(schema) if isinstance(schema, str) else None
        if step is None or schema in seen:
            raise ValueError(f"Unsupported {label} schema.")
        seen.add(schema)
        data = step(dict(data))
    if not isinstance(data, dict):
        raise ValueError(f"Unsupported {label} schema.")
    return data
