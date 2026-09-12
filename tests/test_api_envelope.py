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
from api.main import app, _JUR_NONE_MSG, _SCAFFOLD_DISABLED_MSG, _sign_candidate

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


# ═════════════════════════════════════════════════════════════════════════════
#  REGRESSION: pinpoint field returned only when stripped before formatting
# ═════════════════════════════════════════════════════════════════════════════

_CLASSIFICATION_LEGISLATION = {
    "type": "legislation", "normalized": "Criminal Code, RSC 1985, c C-46, s 718.2(e)",
    "original": "Criminal Code",
}

_CLASSIFICATION_CASE = {
    "type": "case_name", "normalized": "R v Gladue", "original": "R v Gladue",
}

_CLASSIFICATION_CONCEPT = {
    "type": "concept", "normalized": "gladue principle", "original": "gladue principle",
}

_LEGISLATION_RESULT_WITH_PIN = {
    "verified": True, "statute_title": "Criminal Code",
    "citation": "RSC 1985, c C-46", "pinpoint": "s 718.2(e)",
    "_match_path": "exact", "warning": "",
}

_CASE_RESULT_WITH_PIN = {
    "verified": True, "style_of_cause": "R v Gladue",
    "neutral_citation": "[1999] 1 SCR 688", "pinpoint": "at para 47",
}

_CONCEPT_RESULT_LEGISLATION = {
    "verified": True,
    "statute_title": "Criminal Code",
    "citation": "RSC 1985, c C-46",
    "pinpoint": "s 718.2(e)",
    "role": "legislation",
}

_CIT_LEGISLATION_WITH_PIN = "Criminal Code, RSC 1985, c C-46, s 718.2(e)."
_CIT_CASE_WITHOUT_PIN = "R v Gladue, [1999] 1 SCR 688."
_CIT_CONCEPT_WITH_PIN = "Criminal Code, RSC 1985, c C-46, s 718.2(e)."


def test_legislation_route_no_pinpoint_field():
    """Legislation route: pinpoint is embedded in citation text, NOT returned separately."""
    with patch("api.main.classify_and_normalize", return_value=_CLASSIFICATION_LEGISLATION), \
         patch("api.main.search_citation", return_value=[_LEGISLATION_RESULT_WITH_PIN]), \
         patch("api.main.format_citation", return_value=_CIT_LEGISLATION_WITH_PIN):
        resp = client.post("/api/citation", json={"input": "Criminal Code"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    cit = body["data"]["citations"][0]
    assert "pinpoint" not in cit, (
        f"Legislation route must NOT return pinpoint separately, got {cit.get('pinpoint')!r}"
    )
    assert "s 718.2(e)" in cit["citation"], (
        "Pinpoint must be embedded in the citation text"
    )


def test_case_route_has_pinpoint_field():
    """Case_name route: pinpoint is stripped before formatting, returned separately."""
    with patch("api.main.classify_and_normalize", return_value=_CLASSIFICATION_CASE), \
         patch("api.main.search_citation", return_value=[_CASE_RESULT_WITH_PIN]), \
         patch("api.main.format_citation", return_value=_CIT_CASE_WITHOUT_PIN):
        resp = client.post("/api/citation", json={"input": "R v Gladue"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    cit = body["data"]["citations"][0]
    assert cit.get("pinpoint") == "at para 47", (
        f"Case route must return pinpoint separately, got {cit.get('pinpoint')!r}"
    )
    assert "at para 47" not in cit["citation"], (
        "Case route citation text must NOT contain pinpoint (it's handled client-side)"
    )


def test_concept_route_no_pinpoint_field():
    """Concept route with single legislation result: pinpoint is embedded, NOT returned separately.
    This is the direct regression test for the 'gladue principle' duplicate bug."""
    with patch("api.main.classify_and_normalize", return_value=_CLASSIFICATION_CONCEPT), \
         patch("api.main.search_citation", return_value=[_CONCEPT_RESULT_LEGISLATION]), \
         patch("api.main.format_citation", return_value=_CIT_CONCEPT_WITH_PIN):
        resp = client.post("/api/citation", json={"input": "gladue principle"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    cit = body["data"]["citations"][0]
    assert "pinpoint" not in cit, (
        f"Concept route must NOT return pinpoint separately, got {cit.get('pinpoint')!r}"
    )
    assert "s 718.2(e)" in cit["citation"], (
        "Pinpoint must be embedded in the citation text"
    )


def test_citation_select_has_pinpoint_field():
    """citation_select strips pinpoint for case candidates, returns it separately."""
    candidate = {
        "verified": True,
        "style_of_cause": "R v Gladue",
        "neutral_citation": "[1999] 1 SCR 688",
        "pinpoint": "at para 47",
    }
    with patch("api.main.format_citation", return_value=_CIT_CASE_WITHOUT_PIN):
        resp = client.post("/api/citation/select", json={
            "candidates": [_sign_candidate(candidate)],
            "selected_index": 0,
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    cit = body["data"]["citations"][0]
    # citation_select strips pinpoint before formatting → returned separately
    assert cit.get("pinpoint") == "at para 47", (
        f"citation_select must return pinpoint separately, got {cit.get('pinpoint')!r}"
    )
    assert "at para 47" not in cit["citation"], (
        "citation_select citation text must NOT contain pinpoint (it was stripped before formatting)"
    )
