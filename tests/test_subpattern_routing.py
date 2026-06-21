"""Unit tests for deterministic subpattern routing in mcgill_engine.py.

Pure function tests — no LLM calls.
Run:  pytest tests/test_subpattern_routing.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mcgill_engine import select_subpattern, _is_numbered_regulation
from core.mcgill_engine import SUBPATTERN_TEMPLATES, _normalize_title


# ═══════════════════════════════════════════════════════════════════
#  _is_numbered_regulation
# ═══════════════════════════════════════════════════════════════════

def test_numbered_reg_sor():
    assert _is_numbered_regulation("SOR/2000-111") is True

def test_numbered_reg_si():
    assert _is_numbered_regulation("SI/2020-42") is True

def test_numbered_reg_crc():
    assert _is_numbered_regulation("CRC, c 1035") is True

def test_numbered_reg_ontario():
    assert _is_numbered_regulation("O Reg 426/00") is True
    assert _is_numbered_regulation("Ont Reg 123/20") is True

def test_numbered_reg_alberta():
    assert _is_numbered_regulation("Alta Reg 124/2010") is True

def test_numbered_reg_bc():
    assert _is_numbered_regulation("BC Reg 98/2022") is True

def test_numbered_reg_other_provinces():
    assert _is_numbered_regulation("Man Reg 45/99") is True
    assert _is_numbered_regulation("NS Reg 7/2001") is True
    assert _is_numbered_regulation("NB Reg 88/15") is True
    assert _is_numbered_regulation("Nfld Reg 3/92") is True
    assert _is_numbered_regulation("NWT Reg 12/19") is True
    assert _is_numbered_regulation("Nu Reg 5/23") is True
    assert _is_numbered_regulation("PEI Reg 9/18") is True
    assert _is_numbered_regulation("Sask Reg 54/17") is True
    assert _is_numbered_regulation("Yukon Reg 2/21") is True
    assert _is_numbered_regulation("Que Reg 33/20") is True

def test_numbered_reg_descriptive_title_not_matched():
    """Descriptive titles should NOT match as numbered regulations."""
    assert _is_numbered_regulation("Criminal Code") is False
    assert _is_numbered_regulation("Migratory Birds Regulations") is False
    assert _is_numbered_regulation("Youth Criminal Justice Act") is False

def test_numbered_reg_empty():
    assert _is_numbered_regulation("") is False


# ═══════════════════════════════════════════════════════════════════
#  _normalize_title
# ═══════════════════════════════════════════════════════════════════

def test_normalize_title_removes_commas():
    assert _normalize_title("Constitution Act, 1982") == "constitution act 1982"

def test_normalize_title_collapses_spaces():
    assert _normalize_title("Constitution  Act   1982") == "constitution act 1982"

def test_normalize_title_strips():
    assert _normalize_title("  Canadian Charter of Rights and Freedoms  ") == "canadian charter of rights and freedoms"

def test_normalize_title_already_clean():
    assert _normalize_title("canada act 1982") == "canada act 1982"


# ═══════════════════════════════════════════════════════════════════
#  select_subpattern — Jurisprudence
# ═══════════════════════════════════════════════════════════════════

def test_juris_neutral_parallel():
    """neutral_citation + reporter → juris.neutral_parallel"""
    result = select_subpattern("jurisprudence", {
        "style_of_cause": "R v King",
        "neutral_citation": "2002 SCC 10",
        "reporter": "[2002] 1 SCR 227",
    })
    assert result == "juris.neutral_parallel", f"Expected juris.neutral_parallel, got {result}"

def test_juris_neutral_only():
    """neutral_citation only (no reporter) → juris.neutral"""
    result = select_subpattern("jurisprudence", {
        "style_of_cause": "R v King",
        "neutral_citation": "2002 SCC 10",
    })
    assert result == "juris.neutral", f"Expected juris.neutral, got {result}"

def test_juris_reported_only():
    """reporter only (no neutral_citation) → juris.reported_only"""
    result = select_subpattern("jurisprudence", {
        "style_of_cause": "R v Oakes",
        "reporter": "[1986] 1 SCR 103",
    })
    assert result == "juris.reported_only", f"Expected juris.reported_only, got {result}"

def test_juris_unreported():
    """No neutral, no reporter, has database → None (no authoritative example in rules)"""
    result = select_subpattern("jurisprudence", {
        "style_of_cause": "Smith v Jones",
        "database": "CanLII",
        "database_id": "2011 ONSC 1234",
    })
    assert result is None, f"Expected None, got {result}"

def test_juris_unreported_database_id_only():
    """database_id alone (no database key) → None"""
    result = select_subpattern("jurisprudence", {
        "style_of_cause": "Smith v Jones",
        "database_id": "2011 ONSC 1234",
    })
    assert result is None, f"Expected None, got {result}"

def test_juris_no_fields():
    """Only style_of_cause, no citation fields → None"""
    result = select_subpattern("jurisprudence", {
        "style_of_cause": "Smith v Jones",
    })
    assert result is None, f"Expected None, got {result}"

def test_juris_empty_fields():
    """Empty citation fields → None"""
    result = select_subpattern("jurisprudence", {
        "style_of_cause": "Smith v Jones",
        "neutral_citation": "",
        "reporter": "",
    })
    assert result is None, f"Expected None, got {result}"


# ═══════════════════════════════════════════════════════════════════
#  select_subpattern — Legislation
# ═══════════════════════════════════════════════════════════════════

def test_leg_statute():
    """Descriptive title → leg.statute"""
    result = select_subpattern("legislation", {
        "statute_title": "Criminal Code",
        "jurisdiction": "RSC",
        "year": "1985",
        "chapter": "c C-46",
    })
    assert result == "leg.statute", f"Expected leg.statute, got {result}"

def test_leg_constitutional():
    """Title matching CONSTITUTIONAL_TITLES → leg.constitutional (highest priority)"""
    result = select_subpattern("legislation", {
        "statute_title": "Constitution Act, 1982",
        "chapter": "c 11",
        "jurisdiction": "UK",
        "year": "1982",
    })
    assert result == "leg.constitutional", f"Expected leg.constitutional, got {result}"

def test_leg_constitutional_charter():
    """Charter → leg.constitutional"""
    result = select_subpattern("legislation", {
        "statute_title": "Canadian Charter of Rights and Freedoms",
        "chapter": "Schedule B",
        "jurisdiction": "UK",
        "year": "1982",
    })
    assert result == "leg.constitutional", f"Expected leg.constitutional, got {result}"

def test_leg_constitutional_1867():
    """Constitution Act, 1867 → leg.constitutional"""
    result = select_subpattern("legislation", {
        "statute_title": "Constitution Act, 1867",
    })
    assert result == "leg.constitutional", f"Expected leg.constitutional, got {result}"

def test_leg_regulation_numbered():
    """SOR number → None (no authoritative example in rules, falls back to full-topic)"""
    result = select_subpattern("legislation", {
        "statute_title": "SOR/2000-111",
        "jurisdiction": "Canada Gazette Part II",
    })
    assert result is None, f"Expected None, got {result}"

def test_leg_regulation_numbered_ontario():
    """Ontario regulation → None"""
    result = select_subpattern("legislation", {
        "statute_title": "O Reg 426/00",
    })
    assert result is None, f"Expected None, got {result}"

def test_leg_constitutional_no_comma():
    """Constitution Act 1982 (no comma) → leg.constitutional (normalized)"""
    result = select_subpattern("legislation", {
        "statute_title": "Constitution Act 1982",
    })
    assert result == "leg.constitutional", f"Expected leg.constitutional, got {result}"

def test_leg_constitutional_extra_spaces():
    """Constitution  Act,   1982 (extra whitespace) → leg.constitutional"""
    result = select_subpattern("legislation", {
        "statute_title": "Constitution  Act,   1982",
    })
    assert result == "leg.constitutional", f"Expected leg.constitutional, got {result}"

def test_leg_constitutional_lowercase():
    """canadian charter of rights and freedoms (lowercase) → leg.constitutional"""
    result = select_subpattern("legislation", {
        "statute_title": "canadian charter of rights and freedoms",
    })
    assert result == "leg.constitutional", f"Expected leg.constitutional, got {result}"

def test_leg_constitutional_canada_act():
    """Canada Act 1982 (no comma) → leg.constitutional"""
    result = select_subpattern("legislation", {
        "statute_title": "Canada Act 1982",
    })
    assert result == "leg.constitutional", f"Expected leg.constitutional, got {result}"

def test_leg_short_title_not_constitutional():
    """Short form 'Charter' should NOT trigger constitutional (no full title match)."""
    # 'Charter' alone at start doesn't match any CONSTITUTIONAL_TITLES prefix
    result = select_subpattern("legislation", {
        "statute_title": "Charter",  # Not 'Canadian Charter of Rights and Freedoms'
        "jurisdiction": "RSC",
    })
    # Falls through to leg.statute because it has a descriptive title
    assert result == "leg.statute", f"Expected leg.statute, got {result}"

def test_leg_missing_title():
    """No statute_title or title field → None"""
    result = select_subpattern("legislation", {
        "jurisdiction": "RSC",
        "year": "1985",
    })
    assert result is None, f"Expected None, got {result}"

def test_leg_empty_title():
    """Empty title → None"""
    result = select_subpattern("legislation", {
        "statute_title": "",
        "jurisdiction": "RSC",
    })
    assert result is None, f"Expected None, got {result}"


# ═══════════════════════════════════════════════════════════════════
#  select_subpattern — Other types (should always return None)
# ═══════════════════════════════════════════════════════════════════

def test_gov_docs_subtype_parliamentary():
    """government_docs with Hansard/parliamentary text → gov.parliamentary_documents"""
    result = select_subpattern("government_docs", {
        "raw_text": "House of Commons Debates Hansard Official Report",
    })
    assert result == "gov.parliamentary_documents", (
        f"Expected gov.parliamentary_documents, got {result}"
    )

def test_gov_docs_subtype_committee():
    """government_docs with committee text → gov.committee_reports"""
    result = select_subpattern("government_docs", {
        "raw_text": "Standing Committee on Access to Information report on privacy",
    })
    assert result == "gov.committee_reports", (
        f"Expected gov.committee_reports, got {result}"
    )

def test_gov_docs_subtype_inquiry():
    """government_docs with inquiry text → gov.inquiry_reports"""
    result = select_subpattern("government_docs", {
        "raw_text": "Royal Commission of Inquiry into digital platforms final report",
    })
    assert result == "gov.inquiry_reports", (
        f"Expected gov.inquiry_reports, got {result}"
    )

def test_gov_docs_subtype_no_match():
    """government_docs with no matching keywords → None (fall back to full-topic)"""
    result = select_subpattern("government_docs", {
        "raw_text": "Some generic government publication about fisheries management",
    })
    assert result is None, (
        f"Expected None for non-matching gov doc, got {result}"
    )

def test_other_type_secondary_sources():
    result = select_subpattern("secondary_sources.journal_articles", {"author": "Test"})
    assert result is None

def test_other_type_general_rules():
    result = select_subpattern("general_rules", {})
    assert result is None

def test_other_type_constitutional_statutes():
    """constitutional_statutes (top-level) → None; only 'legislation' type routes to leg.*"""
    result = select_subpattern("constitutional_statutes", {
        "statute_title": "Constitution Act, 1982",
    })
    assert result is None


# ═══════════════════════════════════════════════════════════════════
#  SUBPATTERN_TEMPLATES integrity
# ═══════════════════════════════════════════════════════════════════

def test_all_subpattern_templates_have_required_keys():
    """Every entry in SUBPATTERN_TEMPLATES must have 'category' and 'topics'."""
    for key, value in SUBPATTERN_TEMPLATES.items():
        assert "category" in value, f"{key} missing 'category'"
        assert "topics" in value, f"{key} missing 'topics'"
        assert len(value["topics"]) > 0, f"{key} has empty topics"
        for t in value["topics"]:
            assert "topic" in t, f"{key} topic missing 'topic'"
            assert "examples" in t, f"{key} topic missing 'examples'"

def test_regression_original_italic_test_fields():
    """Verify that the fields from eval/italic_test.py still route to expected subpatterns."""
    # Test 1: Case with neutral + reporter → juris.neutral_parallel
    r1 = select_subpattern("jurisprudence", {
        "style_of_cause": "R v Oakes",
        "neutral_citation": "1986 CanLII 46 (SCC)",
        "reporter": "[1986] 1 SCR 103",
    })
    assert r1 == "juris.neutral_parallel", f"R v Oakes should route to neutral_parallel, got {r1}"

    # Test 2: Statute with descriptive title → leg.statute
    r2 = select_subpattern("legislation", {
        "statute_title": "Criminal Code",
        "jurisdiction": "RSC",
        "year": "1985",
        "chapter": "c C-46",
    })
    assert r2 == "leg.statute", f"Criminal Code should route to leg.statute, got {r2}"

    # Test 3: SOR number → None (falls back to full-topic)
    r3 = select_subpattern("legislation", {
        "statute_title": "SOR/2000-111",
        "jurisdiction": "Canada Gazette Part II",
    })
    assert r3 is None, f"SOR/2000-111 should return None, got {r3}"

    # Test 4: Named regulation → leg.statute (has descriptive title)
    r4 = select_subpattern("legislation", {
        "statute_title": "Migratory Birds Regulations",
        "jurisdiction": "CRC",
        "chapter": "c 1035",
    })
    assert r4 == "leg.statute", f"Migratory Birds Regulations should route to leg.statute, got {r4}"
