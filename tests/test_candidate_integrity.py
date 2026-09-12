"""Regression coverage for the signed candidate trust boundary."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import (
    _candidate_signature_valid,
    app,
)


client = TestClient(app)


def _case_candidates(verified=True):
    return [
        {
            "style_of_cause": "R v Alpha",
            "neutral_citation": "2024 SCC 1",
            "verified": verified,
            "role": "case",
        },
        {
            "style_of_cause": "R v Beta",
            "neutral_citation": "2024 SCC 2",
            "verified": verified,
            "role": "case",
        },
    ]


def _issued_candidates(route="case_name", results=None):
    results = results if results is not None else _case_candidates()
    with patch("api.main.rate_limiter.check", return_value=True), \
         patch("api.main.classify_and_normalize", return_value={"type": route}), \
         patch("api.main.search_citation", return_value=results):
        response = client.post("/api/citation", json={"input": "ambiguous source"})
    body = response.json()
    assert body["status"] == "needs_selection"
    return body["data"]["candidates"]


def test_server_issued_candidate_can_be_selected():
    candidates = _issued_candidates()
    with patch("api.main.format_citation", return_value="R v Alpha, 2024 SCC 1."):
        response = client.post("/api/citation/select", json={
            "candidates": candidates,
            "selected_index": 0,
        })

    body = response.json()
    assert body["status"] == "done"
    assert body["data"]["citations"][0]["citation"] == "R v Alpha, 2024 SCC 1."


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verified", True),
        ("neutral_citation", "2099 SCC 999"),
        ("role", "legislation"),
    ],
)
def test_tampered_candidate_metadata_is_rejected_before_formatting(field, value):
    candidates = _issued_candidates(results=_case_candidates(verified=False))
    tampered = deepcopy(candidates)
    tampered[0][field] = value

    with patch("api.main.format_citation") as formatter, \
         patch("api.main._rebuild_bill_citation") as bill_lookup:
        response = client.post("/api/citation/select", json={
            "candidates": tampered,
            "selected_index": 0,
        })

    body = response.json()
    assert body["status"] == "unsupported"
    assert "search again" in body["error"]["reason"].lower()
    formatter.assert_not_called()
    bill_lookup.assert_not_called()


def test_missing_signature_is_rejected():
    candidates = _issued_candidates()
    candidates[0].pop("candidate_signature")
    response = client.post("/api/citation/select", json={
        "candidates": candidates,
        "selected_index": 0,
    })
    assert response.json()["status"] == "unsupported"


def test_non_ascii_signature_is_rejected_without_server_error():
    candidates = _issued_candidates()
    candidates[0]["candidate_signature"] = "中文"
    response = client.post("/api/citation/select", json={
        "candidates": candidates,
        "selected_index": 0,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "unsupported"


def test_candidate_pinpoint_is_integrity_protected():
    candidates = _issued_candidates()
    candidates[0]["pinpoint"] = "at para 12"
    with patch("api.main.format_citation") as formatter:
        response = client.post("/api/citation/select", json={
            "candidates": candidates,
            "selected_index": 0,
        })
    assert response.json()["status"] == "unsupported"
    formatter.assert_not_called()


@pytest.mark.parametrize("route", ["bill", "case_name", "legislation", "citation_number"])
def test_every_direct_needs_selection_branch_signs_all_candidates(route):
    candidates = _issued_candidates(route=route)
    assert all(_candidate_signature_valid(candidate) for candidate in candidates)


def test_concept_needs_selection_branch_signs_all_candidates():
    candidates = _issued_candidates(route="concept")
    assert all(_candidate_signature_valid(candidate) for candidate in candidates)


def test_shared_key_verifies_across_instances_and_rotation_invalidates(monkeypatch):
    from api import main

    monkeypatch.setattr(main, "_CANDIDATE_SIGNING_KEY", b"shared-deployment-key")
    candidate = main._sign_candidate({"display": "R v Alpha", "verified": True})
    assert main._candidate_signature_valid(candidate)

    monkeypatch.setattr(main, "_CANDIDATE_SIGNING_KEY", b"rotated-deployment-key")
    assert not main._candidate_signature_valid(candidate)
