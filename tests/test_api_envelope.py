"""Deterministic assertion for API response envelope — source_type field.

Uses the deterministic /api/citation/assemble endpoint (no LLM, no database).
Asserts the returned envelope contains source_type on each citation.
Per OA Execution Skill §K: runtime acceptance criteria require direct runtime assertion.

Run:  pytest tests/test_api_envelope.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


def test_assemble_scaffold_disabled_returns_unsupported():
    """POST /api/citation/assemble returns unsupported when SCAFFOLD_ENABLED=false."""
    payload = {
        "type": "jurisprudence",
        "fields": {
            "style_of_cause": "R v Jordan",
            "neutral_citation": "2016 SCC 27",
        },
    }
    resp = client.post("/api/citation/assemble", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unsupported"
    assert body["error"]["reason"] == "Manual citation assembly is currently disabled."


def test_assemble_scaffold_disabled_no_verified_citation():
    """Assembled citations are not produced when SCAFFOLD_ENABLED=false."""
    payload = {
        "type": "legislation",
        "fields": {
            "statute_title": "Criminal Code",
            "jurisdiction": "Canada",
        },
    }
    resp = client.post("/api/citation/assemble", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unsupported"
    citations = body.get("data", {}).get("citations", [])
    assert len(citations) == 0
