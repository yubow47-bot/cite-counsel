"""Regression tests for pinpoint duplication bug (SCC 2025-003).

Root cause: two independent code paths produce legislation-route citation
candidates with inconsistent field schemas, and one API endpoint applies
a case-only UI behavior indiscriminately to all routes.

Bug 1: _verify_legislation() returned ad-hoc 'name' and 'neutral_citation'
       fields alongside the clean legislation schema.  The 'name' field
       embeded citation text with the pinpoint, while 'pinpoint' was also
       returned as a separate field — causing format_citation() to emit the
       pinpoint twice.

Bug 2: citation_select() unconditionally stripped 'pinpoint' before formatting
       and returned it as a separate field for ANY candidate type, instead of
       only case/jurisprudence candidates.  This caused legislation candidates
       via the concept route to show the pinpoint both in the formatted citation
       text AND as a separate editable UI field.

These tests verify the fix for both bugs.

Run: pytest tests/test_pinpoint_regression.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


# ═══════════════════════════════════════════════════════════════════
# Test 1: "ccc s.718(e)" — direct legislation route
# ═══════════════════════════════════════════════════════════════════

def test_direct_legislation_pinpoint_once():
    """'ccc s.718(e)' → direct legislation route → citation contains
    's 718(e)' exactly ONCE, no separate pinpoint field."""
    classification = {
        "type": "legislation",
        "normalized": "Criminal Code, RSC 1985, c C-46, s 718(e)",
        "original": "ccc s.718(e)",
    }
    result = {
        "verified": True,
        "statute_title": "Criminal Code",
        "jurisdiction": None,
        "chapter": "c C-46",
        "pinpoint": "s 718(e)",
        "citation": "RSC 1985, c C-46",
        "warning": "",
    }
    formatted = "Criminal Code, RSC 1985, c C-46, s 718(e)."

    with (
        patch("api.main.classify_and_normalize", return_value=classification),
        patch("api.main.search_citation", return_value=[result]),
        patch("api.main.format_citation", return_value=formatted),
    ):
        resp = client.post("/api/citation", json={"input": "ccc s.718(e)"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    cit = body["data"]["citations"][0]
    assert "pinpoint" not in cit, (
        f"Direct legislation must NOT return separate pinpoint, "
        f"got {cit.get('pinpoint')!r}"
    )
    assert cit["citation"].count("s 718(e)") == 1, (
        f"Pinpoint must appear exactly ONCE in citation text: {cit['citation']!r}"
    )


# ═══════════════════════════════════════════════════════════════════
# Test 2: "gladue principle" → concept → citation_select
# ═══════════════════════════════════════════════════════════════════

def test_concept_legislation_via_select_pinpoint_once():
    """'gladue principle' → concept → legislation candidate via
    /api/citation/select → citation contains 's 718.2(e)' exactly ONCE,
    no separate pinpoint key."""
    # Shape after _verify_legislation fix: clean schema with role preserved.
    candidate = {
        "statute_title": "Criminal Code",
        "citation": "RSC 1985, c C-46",
        "pinpoint": "s 718.2(e)",
        "role": "legislation",
        "verified": True,
    }
    formatted = "Criminal Code, RSC 1985, c C-46, s 718.2(e)."

    with patch("api.main.format_citation", return_value=formatted):
        resp = client.post("/api/citation/select", json={
            "candidates": [candidate],
            "selected_index": 0,
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    cit = body["data"]["citations"][0]
    assert "pinpoint" not in cit, (
        f"Concept legislation via citation_select must NOT return separate pinpoint, "
        f"got {cit.get('pinpoint')!r}"
    )
    assert cit["citation"].count("s 718.2(e)") == 1, (
        f"Pinpoint must appear exactly ONCE: {cit['citation']!r}"
    )


# ═══════════════════════════════════════════════════════════════════
# Test 3: case_name via citation_select — existing behavior locked
# ═══════════════════════════════════════════════════════════════════

def test_case_citation_select_has_pinpoint_field():
    """Case/jurisprudence via citation_select → pinpoint stripped before
    formatting, returned as separate 'pinpoint' field.
    This locks in the existing behavior for case candidates."""
    candidate = {
        "style_of_cause": "R v Gladue",
        "neutral_citation": "[1999] 1 SCR 688",
        "pinpoint": "at para 47",
        "verified": True,
    }
    formatted = "R v Gladue, [1999] 1 SCR 688."

    with patch("api.main.format_citation", return_value=formatted):
        resp = client.post("/api/citation/select", json={
            "candidates": [candidate],
            "selected_index": 0,
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    cit = body["data"]["citations"][0]
    assert cit.get("pinpoint") == "at para 47", (
        f"Case via citation_select must return pinpoint separately, "
        f"got {cit.get('pinpoint')!r}"
    )
    assert "at para 47" not in cit["citation"], (
        "Case citation_select citation must NOT contain pinpoint "
        "(it was stripped before formatting)"
    )


# ═══════════════════════════════════════════════════════════════════
# Test 4: _verify_legislation() clean schema unit test
# ═══════════════════════════════════════════════════════════════════

def test_verify_legislation_clean_schema():
    """_verify_legislation() returns clean legislation-schema keys
    (statute_title, jurisdiction, chapter, pinpoint, citation, verified)
    and does NOT return ad-hoc keys (name, neutral_citation).
    'pinpoint' text appears in exactly one field of the returned dict."""
    from local_tools import citation_search as cs_mod

    mock_llm = {
        "candidates": [
            {
                "name": "Criminal Code, RSC 1985, c C-46, s 718.2(e)",
                "citation": None,
                "type": "legislation",
            }
        ]
    }

    mock_a2aj_results = {
        "results": [
            {
                "name_en": "Criminal Code",
                "citation_en": "RSC 1985, c C-46",
                "dataset": "CA",
            }
        ]
    }

    with patch.object(cs_mod, "call_gemini_text_structured", return_value=mock_llm):
        with patch("local_tools.utils.request_with_retry") as mock_request:
            mock_resp = mock_request.return_value
            mock_resp.json.return_value = mock_a2aj_results

            results = cs_mod.expand_concept("test concept")

    # Must have one candidate
    assert len(results) == 1
    r = results[0]

    # Role preserved (required by detect_type routing)
    assert r.get("role") == "legislation", (
        f"role must be 'legislation', got {r.get('role')!r}"
    )

    # Clean schema keys present
    assert "statute_title" in r, f"Key set: {list(r.keys())}"
    assert "pinpoint" in r, f"Key set: {list(r.keys())}"
    assert "citation" in r, f"Key set: {list(r.keys())}"
    assert "verified" in r, f"Key set: {list(r.keys())}"

    # Ad-hoc keys NOT present
    assert "name" not in r, (
        f"ad-hoc 'name' must NOT be present, got {r.get('name')!r}"
    )
    assert "neutral_citation" not in r, (
        f"ad-hoc 'neutral_citation' must NOT be present, got {r.get('neutral_citation')!r}"
    )

    # Verify 'pinpoint' text appears in exactly ONE field of the dict
    pinpoint_text = r.get("pinpoint", "")
    assert pinpoint_text, "pinpoint should be non-empty"
    pin_count = sum(
        1 for v in r.values()
        if isinstance(v, str) and pinpoint_text in v
    )
    assert pin_count == 1, (
        f"Pinpoint text {pinpoint_text!r} appears in {pin_count} fields "
        f"(expected exactly 1). Fields: {r}"
    )
