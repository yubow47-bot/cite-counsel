"""Saved research and inquiry objects upgrade on read without being rewritten."""
import json

import pytest

from core import inquiry, research
from core.tool_contracts import Artifact as _Artifact, Derivation, Field


def Artifact(kind, content):
    return _Artifact(kind, content, Derivation((Field(content, "model"),), "test"))


def old_research():
    return Artifact("legal_research", json.dumps({"schema": "legal_research.v0", "type": "claim", "text": "Old"}))


def test_unknown_or_future_schema_is_rejected():
    with pytest.raises(ValueError, match="Unsupported legal research schema"):
        research.unpack(old_research())
    future = Artifact("inquiry", json.dumps({"schema": "inquiry.v9", "question": "?"}))
    with pytest.raises(ValueError, match="Unsupported inquiry schema"):
        inquiry.unpack(future)
    with pytest.raises(ValueError, match="Unsupported inquiry schema"):
        inquiry.unpack(Artifact("inquiry", json.dumps(["not", "an", "object"])))


def test_registered_migration_upgrades_only_the_read_copy(monkeypatch):
    monkeypatch.setitem(research.MIGRATIONS, "legal_research.v0",
                        lambda data: {**data, "schema": research.SCHEMA, "migrated": True})
    artifact = old_research()
    stored = artifact.content
    data = research.unpack(artifact)
    assert data["schema"] == research.SCHEMA and data["migrated"] and data["text"] == "Old"
    assert artifact.content == stored


def test_migration_loop_is_rejected(monkeypatch):
    monkeypatch.setitem(inquiry.MIGRATIONS, "inquiry.v0", lambda data: {**data, "schema": "inquiry.v0"})
    with pytest.raises(ValueError, match="Unsupported inquiry schema"):
        inquiry.unpack(Artifact("inquiry", json.dumps({"schema": "inquiry.v0"})))
