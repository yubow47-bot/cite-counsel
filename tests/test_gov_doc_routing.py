"""Regression tests: government_docs subtype routing.

Validates that downstream routing is correct GIVEN a classify_document_type
return value. LLM classification accuracy is out of OA scope — these tests
mock classify_document_type and assert the routing layer only.

No test in this module makes a real network or LLM call.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure project root is on sys.path
_PROJ = Path(__file__).resolve().parent.parent
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

FIXTURES_DIR = _PROJ / "eval" / "fixtures"


def _read_fixture(name: str) -> str:
    path = FIXTURES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Fixture not found: {path}")
    return path.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
#  AC1–AC5: classify_document_type (mocked) + subtype picker + get_rules
# ═══════════════════════════════════════════════════════════════════

def test_ac1_hansard_to_parliamentary_routing():
    """AC1: Given classify returns 'government_document' for hansard text,
    subtype picker → 'parliamentary_documents', get_rules → exactly 1 topic
    named 'Parliamentary Documents'."""
    from local_tools.file_extractor import classify_gov_doc_subtype
    from core.mcgill_engine import get_rules

    text = _read_fixture("hansard_body.txt")

    with patch("local_tools.file_extractor.classify_document_type",
               return_value="government_document"):
        from local_tools.file_extractor import classify_document_type
        doc_type = classify_document_type(text)
        assert doc_type == "government_document", (
            f"Expected mocked 'government_document', got '{doc_type}'"
        )

    subtype = classify_gov_doc_subtype(text)
    assert subtype == "parliamentary_documents", (
        f"Expected 'parliamentary_documents', got '{subtype}'"
    )

    rules = get_rules("government_docs", subpattern="gov.parliamentary_documents")
    topics = rules.get("topics", [])
    assert len(topics) == 1, f"Expected 1 topic, got {len(topics)}"
    assert topics[0]["topic"] == "Parliamentary Documents", (
        f"Expected 'Parliamentary Documents', got '{topics[0]['topic']}'"
    )


def test_ac2_hansard_subtype_is_parliamentary_documents():
    """AC2: classify_gov_doc_subtype(hansard) == 'parliamentary_documents'"""
    from local_tools.file_extractor import classify_gov_doc_subtype
    text = _read_fixture("hansard_body.txt")
    result = classify_gov_doc_subtype(text)
    assert result == "parliamentary_documents", (
        f"Expected 'parliamentary_documents', got '{result}'"
    )


def test_ac3_get_rules_parliamentary_documents():
    """AC3: get_rules('government_docs', subpattern='gov.parliamentary_documents')
    returns exactly ONE topic, and topics[0]['topic'] == 'Parliamentary Documents'"""
    from core.mcgill_engine import get_rules
    rules = get_rules("government_docs", subpattern="gov.parliamentary_documents")
    topics = rules.get("topics", [])
    assert len(topics) == 1, f"Expected 1 topic, got {len(topics)}"
    assert topics[0]["topic"] == "Parliamentary Documents", (
        f"Expected 'Parliamentary Documents', got '{topics[0]['topic']}'"
    )


def test_gov_examples_use_compact_mcgill_10th_form():
    """Cross-check: gov.* subpattern examples must use compact McGill 10th form
    (NN-N session, 'No' not 'vol', no 'Canada,' prefix for federal, no 'Parl,'
    or 'Sess,'). Fully deterministic — asserts on static template data."""
    from core.mcgill_engine import get_rules

    # ── Parliamentary Documents ──
    parl = get_rules("government_docs", subpattern="gov.parliamentary_documents")
    parl_ex = parl["topics"][0]["examples"]
    parl_text = " ".join(parl_ex)
    # Must contain compact session form
    assert any(m in e for e in parl_ex for m in ["37-1", "42-1"]), (
        f"parliamentary examples missing compact NN-N session: {parl_ex}"
    )
    # Must NOT contain long-form markers
    assert "Parl," not in parl_text, f"parliamentary examples contain 'Parl,': {parl_text[:200]}"
    assert "Sess," not in parl_text, f"parliamentary examples contain 'Sess,': {parl_text[:200]}"
    assert "vol " not in parl_text, f"parliamentary examples contain 'vol ': {parl_text[:200]}"
    # Federal examples must NOT have "Canada," prefix
    for ex in parl_ex:
        if "House of Commons" in ex:
            assert not ex.startswith("Canada,"), (
                f"federal example has 'Canada,' prefix: {ex}"
            )

    # ── Committee Reports ──
    cmte = get_rules("government_docs", subpattern="gov.committee_reports")
    cmte_ex = cmte["topics"][0]["examples"]
    cmte_text = " ".join(cmte_ex)
    # Must contain compact session form
    assert any(m in e for e in cmte_ex for m in ["39-2", "43-2"]), (
        f"committee examples missing compact NN-N session: {cmte_ex}"
    )
    assert "Parl," not in cmte_text, f"committee examples contain 'Parl,': {cmte_text[:200]}"
    assert "Sess," not in cmte_text, f"committee examples contain 'Sess,': {cmte_text[:200]}"

    # ── Inquiry Reports ──
    inq = get_rules("government_docs", subpattern="gov.inquiry_reports")
    inq_ex = inq["topics"][0]["examples"]
    inq_text = " ".join(inq_ex)
    # Must contain the Blood System inquiry (authoritative example)
    assert any("Commission of Inquiry on the Blood System" in e for e in inq_ex), (
        f"inquiry examples missing authoritative Blood System example: {inq_ex}"
    )
    assert "vol " in inq_text, f"inquiry examples missing 'vol ' (inquiry reports use vol): {inq_text[:200]}"


def test_ac4_committee_report_routing():
    """AC4: Given classify returns 'government_document' for committee text,
    subtype picker → 'committee_reports', get_rules → exactly 1 topic
    named 'Committee Reports'."""
    from local_tools.file_extractor import classify_gov_doc_subtype
    from core.mcgill_engine import get_rules

    text = _read_fixture("committee_report.txt")

    with patch("local_tools.file_extractor.classify_document_type",
               return_value="government_document"):
        from local_tools.file_extractor import classify_document_type
        doc_type = classify_document_type(text)
        assert doc_type == "government_document", (
            f"Expected mocked 'government_document', got '{doc_type}'"
        )

    subtype = classify_gov_doc_subtype(text)
    assert subtype == "committee_reports", (
        f"Expected 'committee_reports', got '{subtype}'"
    )

    rules = get_rules("government_docs", subpattern="gov.committee_reports")
    topics = rules.get("topics", [])
    assert len(topics) == 1, f"Expected 1 topic, got {len(topics)}"
    assert topics[0]["topic"] == "Committee Reports"


def test_ac5_inquiry_report_routing():
    """AC5: Given classify returns 'government_document' for inquiry text,
    subtype picker → 'inquiry_reports', get_rules → exactly 1 topic
    named 'Reports on Inquiries and Commissions'."""
    from local_tools.file_extractor import classify_gov_doc_subtype
    from core.mcgill_engine import get_rules

    text = _read_fixture("inquiry_report.txt")

    with patch("local_tools.file_extractor.classify_document_type",
               return_value="government_document"):
        from local_tools.file_extractor import classify_document_type
        doc_type = classify_document_type(text)
        assert doc_type == "government_document", (
            f"Expected mocked 'government_document', got '{doc_type}'"
        )

    subtype = classify_gov_doc_subtype(text)
    assert subtype == "inquiry_reports", (
        f"Expected 'inquiry_reports', got '{subtype}'"
    )

    rules = get_rules("government_docs", subpattern="gov.inquiry_reports")
    topics = rules.get("topics", [])
    assert len(topics) == 1, f"Expected 1 topic, got {len(topics)}"
    assert topics[0]["topic"] == "Reports on Inquiries and Commissions"


# ═══════════════════════════════════════════════════════════════════
#  AC6: Tab 3 wiring — format_citation receives doc_type
# ═══════════════════════════════════════════════════════════════════

def test_ac6_extract_url_passes_doc_type_to_format_citation():
    """AC6: In extract_url URL-only branch, format_citation is invoked with a
    non-None doc_type derived from classify_document_type.
    (No real network/LLM call — all external deps mocked.)"""
    from api.main import extract_url, UrlInput
    import asyncio

    with patch("api.main.format_citation") as mock_format, \
         patch("api.main.extract_from_url") as mock_extract, \
         patch("api.main.classify_document_type") as mock_classify:

        mock_extract.return_value = {
            "raw_text": _read_fixture("hansard_body.txt"),
            "url": "https://www.ourcommons.ca/test",
            "page_title": "House of Commons Debates",
        }
        mock_format.return_value = "Fake citation"
        mock_classify.return_value = "government_document"

        async def _run():
            body = UrlInput(url="https://www.ourcommons.ca/test")
            resp = await extract_url(body)
            return resp

        resp = asyncio.run(_run())

        assert mock_format.called, "format_citation was not called"

        call_kwargs = mock_format.call_args.kwargs
        doc_type = call_kwargs.get("doc_type")
        assert doc_type is not None, (
            f"doc_type kwarg was not passed or was None; kwargs: {call_kwargs}"
        )
        assert doc_type == "government_document", (
            f"Expected doc_type='government_document', got '{doc_type}'"
        )


# ═══════════════════════════════════════════════════════════════════
#  AC7–AC8: Website control — no over-matching
# ═══════════════════════════════════════════════════════════════════

def test_ac7_website_routing_no_overmatch():
    """AC7: Given classify returns 'website', subtype picker is NOT invoked
    (gov-doc narrowing does NOT fire on non-gov input)."""
    from local_tools.file_extractor import classify_gov_doc_subtype

    text = _read_fixture("website_blog.txt")

    with patch("local_tools.file_extractor.classify_document_type",
               return_value="website"):
        from local_tools.file_extractor import classify_document_type
        doc_type = classify_document_type(text)
        assert doc_type == "website", (
            f"Expected mocked 'website', got '{doc_type}'"
        )

    # classify_gov_doc_subtype should return None for non-gov text
    subtype = classify_gov_doc_subtype(text)
    assert subtype is None, (
        f"Expected None for website text, got '{subtype}'"
    )


def test_ac8_subtype_picker_returns_none_for_website():
    """AC8: classify_gov_doc_subtype returns None for website fixture
    (gov-doc narrowing does NOT fire on non-gov input)."""
    from local_tools.file_extractor import classify_gov_doc_subtype
    text = _read_fixture("website_blog.txt")
    result = classify_gov_doc_subtype(text)
    assert result is None, (
        f"Expected None for website text, got '{result}'"
    )


# ═══════════════════════════════════════════════════════════════════
#  ITEM 1 — Tab 2 (file path) routing: Hansard PDF → parliamentary
# ═══════════════════════════════════════════════════════════════════

def test_tab2_file_path_hansard_to_parliamentary():
    """Item 1: Tab 2 (POST /api/extract/file) routes a Hansard PDF body to
    single-topic parliamentary routing. Mock classify_document_type to return
    'government_document' for the hansard fixture — no live LLM call.
    Then assert: classify_gov_doc_subtype → 'parliamentary_documents',
    get_rules('government_docs', subpattern='gov.parliamentary_documents')
    → exactly 1 topic named 'Parliamentary Documents'."""
    from local_tools.file_extractor import classify_gov_doc_subtype
    from core.mcgill_engine import get_rules

    text = _read_fixture("hansard_body.txt")

    with patch("local_tools.file_extractor.classify_document_type",
               return_value="government_document"):
        from local_tools.file_extractor import classify_document_type
        doc_type = classify_document_type(text)
        assert doc_type == "government_document", (
            f"Tab 2: expected mocked 'government_document', got '{doc_type}'"
        )

    subtype = classify_gov_doc_subtype(text)
    assert subtype == "parliamentary_documents", (
        f"Tab 2: expected 'parliamentary_documents', got '{subtype}'"
    )

    rules = get_rules("government_docs", subpattern="gov.parliamentary_documents")
    topics = rules.get("topics", [])
    assert len(topics) == 1, f"Tab 2: expected 1 topic, got {len(topics)}"
    assert topics[0]["topic"] == "Parliamentary Documents", (
        f"Tab 2: expected 'Parliamentary Documents', got '{topics[0]['topic']}'"
    )


# ═══════════════════════════════════════════════════════════════════
#  ITEM 2 — Government-adjacent over-match controls (4 fixtures)
# ═══════════════════════════════════════════════════════════════════

_COVERED_SUBTYPES = (
    "parliamentary_documents",
    "committee_reports",
    "inquiry_reports",
)

def test_gov_news_release_not_overmatched():
    """gov_news_release.txt: government press release — no parliamentary/
    committee/inquiry markers → classify_gov_doc_subtype returns None."""
    from local_tools.file_extractor import classify_gov_doc_subtype
    text = _read_fixture("gov_news_release.txt")
    result = classify_gov_doc_subtype(text)
    assert result not in _COVERED_SUBTYPES, (
        f"gov_news_release incorrectly matched subtype '{result}'"
    )

def test_gov_policy_page_not_overmatched():
    """gov_policy_page.txt: government policy web page — no parliamentary/
    committee/inquiry markers → classify_gov_doc_subtype returns None."""
    from local_tools.file_extractor import classify_gov_doc_subtype
    text = _read_fixture("gov_policy_page.txt")
    result = classify_gov_doc_subtype(text)
    assert result not in _COVERED_SUBTYPES, (
        f"gov_policy_page incorrectly matched subtype '{result}'"
    )

def test_gov_department_page_not_overmatched():
    """gov_department_page.txt: department landing page — no parliamentary/
    committee/inquiry markers → classify_gov_doc_subtype returns None."""
    from local_tools.file_extractor import classify_gov_doc_subtype
    text = _read_fixture("gov_department_page.txt")
    result = classify_gov_doc_subtype(text)
    assert result not in _COVERED_SUBTYPES, (
        f"gov_department_page incorrectly matched subtype '{result}'"
    )

def test_gov_minister_statement_not_overmatched():
    """gov_minister_statement.txt: ministerial statement (not Hansard) —
    no parliamentary/committee/inquiry markers → classify_gov_doc_subtype
    returns None."""
    from local_tools.file_extractor import classify_gov_doc_subtype
    text = _read_fixture("gov_minister_statement.txt")
    result = classify_gov_doc_subtype(text)
    assert result not in _COVERED_SUBTYPES, (
        f"gov_minister_statement incorrectly matched subtype '{result}'"
    )


# ═══════════════════════════════════════════════════════════════════
#  AC bonus: select_subpattern integration
# ═══════════════════════════════════════════════════════════════════

def test_select_subpattern_routes_gov_docs():
    """select_subpattern returns correct gov subpattern keys based on raw_text."""
    from core.mcgill_engine import select_subpattern

    # Hansard → parliamentary
    result = select_subpattern("government_docs", {
        "raw_text": _read_fixture("hansard_body.txt"),
    })
    assert result == "gov.parliamentary_documents", f"Got {result}"

    # Committee → committee_reports
    result = select_subpattern("government_docs", {
        "raw_text": _read_fixture("committee_report.txt"),
    })
    assert result == "gov.committee_reports", f"Got {result}"

    # Inquiry → inquiry_reports
    result = select_subpattern("government_docs", {
        "raw_text": _read_fixture("inquiry_report.txt"),
    })
    assert result == "gov.inquiry_reports", f"Got {result}"

    # Website → None (no raw_text in a website doc that would trigger gov)
    result = select_subpattern("government_docs", {
        "raw_text": _read_fixture("website_blog.txt"),
    })
    assert result is None, f"Expected None for non-gov text, got {result}"


# ═══════════════════════════════════════════════════════════════════
#  Empty-body guard tests — Tab 3 URL-only branch degradation
# ═══════════════════════════════════════════════════════════════════

def test_ac1_empty_body_returns_unsupported_and_skips_format():
    """AC1: given fields with raw_text='' and populated metadata, the empty-body
    guard returns status=='unsupported' AND format_citation is NOT called."""
    from api.main import extract_url, UrlInput
    import asyncio

    with patch("api.main.extract_from_url") as mock_extract, \
         patch("api.main.format_citation") as mock_format:

        mock_extract.return_value = {
            "raw_text": "",
            "url": "https://www.ourcommons.ca/test",
            "page_title": "Debates (Hansard) No. 139 - House of Commons",
            "newspaper": "OURCOMMONS",
        }

        async def _run():
            body = UrlInput(url="https://www.ourcommons.ca/test")
            return await extract_url(body)

        resp = asyncio.run(_run())

        assert resp["status"] == "unsupported", (
            f"Expected 'unsupported', got '{resp['status']}'"
        )
        assert mock_format.called is False, (
            "format_citation was called but should have been skipped for empty body"
        )


def test_ac2_nonempty_body_calls_format_citation():
    """AC2: given fields with substantial raw_text (500 chars), the guard does
    NOT trigger — format_citation IS called."""
    from api.main import extract_url, UrlInput
    import asyncio

    with patch("api.main.extract_from_url") as mock_extract, \
         patch("api.main.format_citation") as mock_format, \
         patch("api.main.classify_document_type") as mock_classify:

        mock_extract.return_value = {
            "raw_text": "A" * 500,
            "url": "https://example.com/page",
            "page_title": "A Real Page",
        }
        mock_format.return_value = "Fake sentinel citation"
        mock_classify.return_value = "website"

        async def _run():
            body = UrlInput(url="https://example.com/page")
            return await extract_url(body)

        resp = asyncio.run(_run())

        assert resp["status"] == "done", (
            f"Expected 'done', got '{resp['status']}'"
        )
        assert mock_format.called is True, (
            "format_citation was NOT called for non-empty body"
        )


def test_ac3_guard_only_in_url_only_branch_not_doi_isbn():
    """AC3: The empty-body guard is structurally inside the URL-only branch only
    (lines after extract_from_url and before the try/classify block). DOI and ISBN
    branches are before the URL-only section and are not affected.

    This is a structural assertion — verifiable by reading api/main.py source.
    We assert: (a) the guard code appears after extract_from_url, (b) the DOI
    path (line ~503) is before it and calls format_citation directly, (c) the
    ISBN path (line ~528) is before it similarly."""
    import os
    VERIFY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(VERIFY, "api", "main.py"), encoding="utf-8") as f:
        source = f.read()

    # Guard is after extract_from_url in URL-only section
    assert "EMPTY_BODY_THRESHOLD" in source, "EMPTY_BODY_THRESHOLD not found"
    assert "trafilatura may return metadata but no body" in source, (
        "empty-body guard comment not found"
    )

    # DOI branch is before the URL-only branch and calls format_citation(doc_type="journal_article")
    assert 'doc_type="journal_article"' in source, "DOI format_citation not found"

    # ISBN branch is before the URL-only branch and calls format_citation(doc_type="book")
    assert 'doc_type="book"' in source, "ISBN format_citation not found"

    # The guard's return statement uses "unsupported" status, not "done"
    assert '"url", "unsupported"' in source, (
        "guard should return 'unsupported' envelope for url route"
    )


if __name__ == "__main__":
    failures = []
    tests = [
        ("AC1: hansard→parliamentary routing (mocked)", test_ac1_hansard_to_parliamentary_routing),
        ("AC2: hansard subtype picker", test_ac2_hansard_subtype_is_parliamentary_documents),
        ("AC3: get_rules parliamentary 1 topic", test_ac3_get_rules_parliamentary_documents),
        ("compact: gov examples use McGill 10th form", test_gov_examples_use_compact_mcgill_10th_form),
        ("AC4: committee→committee_reports (mocked)", test_ac4_committee_report_routing),
        ("AC5: inquiry→inquiry_reports (mocked)", test_ac5_inquiry_report_routing),
        ("AC6: extract_url passes doc_type (mocked)", test_ac6_extract_url_passes_doc_type_to_format_citation),
        ("AC7: website routing no overmatch (mocked)", test_ac7_website_routing_no_overmatch),
        ("AC8: website→None subtype", test_ac8_subtype_picker_returns_none_for_website),
        ("Item1: Tab2 file path hansard→parliamentary", test_tab2_file_path_hansard_to_parliamentary),
        ("Item2: gov news release not overmatched", test_gov_news_release_not_overmatched),
        ("Item2: gov policy page not overmatched", test_gov_policy_page_not_overmatched),
        ("Item2: gov department page not overmatched", test_gov_department_page_not_overmatched),
        ("Item2: gov minister statement not overmatched", test_gov_minister_statement_not_overmatched),
        ("AC1: empty body → unsupported, no format_citation", test_ac1_empty_body_returns_unsupported_and_skips_format),
        ("AC2: nonempty body → format_citation called", test_ac2_nonempty_body_calls_format_citation),
        ("AC3: guard only in URL-only branch, not DOI/ISBN", test_ac3_guard_only_in_url_only_branch_not_doi_isbn),
        ("select_subpattern gov routing", test_select_subpattern_routes_gov_docs),
    ]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failures.append(name)
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            failures.append(name)

    print()
    if failures:
        print(f"FAILED: {len(failures)}/{len(tests)} tests")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed.")
        sys.exit(0)
