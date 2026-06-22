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


def test_assemble_response_contains_source_type():
    """POST /api/citation/assemble returns source_type on citation objects."""
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
    assert body["status"] == "done"
    citations = body.get("data", {}).get("citations", [])
    assert len(citations) >= 1
    for cit in citations:
        assert "source_type" in cit, (
            f"Each citation must carry source_type; missing in {cit}"
        )
        assert cit["source_type"] == "jurisprudence", (
            f"Expected source_type='jurisprudence', got '{cit.get('source_type')}'"
        )


def test_assemble_response_verified_false_and_source_type():
    """Assembled citations carry both verified=False and source_type."""
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
    citations = body.get("data", {}).get("citations", [])
    assert len(citations) >= 1
    cit = citations[0]
    assert cit.get("verified") is False
    assert cit.get("source_type") == "legislation"
