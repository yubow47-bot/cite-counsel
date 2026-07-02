"""Deterministic assertion for API response envelope — source_type field.

Uses the deterministic /api/citation/assemble endpoint (no LLM, no database).
Asserts the returned envelope contains source_type on each citation.
Per OA Execution Skill §K: runtime acceptance criteria require direct runtime assertion.

Run:  pytest tests/test_api_envelope.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from fastapi.testclient import TestClient
from api.main import app, _JUR_NONE_MSG, _SCAFFOLD_DISABLED_MSG

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


# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: jur_none hint message
# ═════════════════════════════════════════════════════════════════════════════

_CLASSIFICATION_LEGISLATION = {
    "type": "legislation", "normalized": "Some Act", "original": "Some Act",
}

_RESULT_JUR_NONE = {
    "verified": False, "_match_path": "jur_none",
    "statute_title": "Some Act", "warning": "",
}

_RESULT_NO_EXACT_MATCH = {
    "verified": False, "_match_path": "no_exact_match",
    "statute_title": "Some Act", "warning": "",
}


def test_jur_none_shows_specific_message():
    """Legislation query with _match_path=jur_none returns the jurisdiction hint."""
    with patch("api.main.classify_and_normalize", return_value=_CLASSIFICATION_LEGISLATION), \
         patch("api.main.search_citation", return_value=[_RESULT_JUR_NONE]):
        resp = client.post("/api/citation", json={"input": "Some Act"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unsupported", (
        f"Expected unsupported status, got {body['status']!r}"
    )
    assert body["error"]["reason"] == _JUR_NONE_MSG, (
        f"Expected jur_none hint message, got {body['error']['reason']!r}"
    )


def test_other_match_path_shows_generic_message():
    """Legislation query with _match_path=no_exact_match returns the generic message."""
    with patch("api.main.classify_and_normalize", return_value=_CLASSIFICATION_LEGISLATION), \
         patch("api.main.search_citation", return_value=[_RESULT_NO_EXACT_MATCH]):
        resp = client.post("/api/citation", json={"input": "Some Act"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unsupported", (
        f"Expected unsupported status, got {body['status']!r}"
    )
    assert body["error"]["reason"] == _SCAFFOLD_DISABLED_MSG, (
        f"Expected generic disabled message, got {body['error']['reason']!r}"
    )
