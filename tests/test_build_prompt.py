"""Deterministic assertion for build_prompt() anti-pinpoint rule.

Pure function test — no LLM, no I/O beyond mcgill_rules.json.
Asserts the returned prompt string contains the expected STRICT OUTPUT RULE.
This is the deterministic assertion target for Ticket 1 (per OA Execution Skill D.1).

Run:  pytest tests/test_build_prompt.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mcgill_engine import build_prompt


def test_build_prompt_contains_anti_pinpoint_rule():
    """build_prompt() output MUST contain the pinpoint prohibition rule."""
    fields = {
        "style_of_cause": "R v Jordan",
        "neutral_citation": "2016 SCC 27",
    }
    rules = {
        "category": "Jurisprudence — Neutral Citation Only",
        "topics": [
            {
                "topic": "Neutral Citation (no parallel)",
                "template": "",
                "examples": ["R v King, 2002 SCC 10."],
            }
        ],
    }
    prompt = build_prompt(fields, "jurisprudence", rules, subpattern="juris.neutral")

    assert "NEVER add a pinpoint" in prompt, (
        "STRICT OUTPUT RULES must contain the anti-pinpoint instruction"
    )


def test_build_prompt_anti_pinpoint_rule_format():
    """The pinpoint rule must specifically reference non-null input fields."""
    fields = {"statute_title": "Criminal Code", "jurisdiction": "Canada"}
    rules = {
        "category": "Legislation — Statutes",
        "topics": [
            {
                "topic": "Statutes – General Form",
                "template": "Title, statute volume jurisdiction, year, chapter, pinpoint",
            }
        ],
    }
    prompt = build_prompt(fields, "legislation", rules, subpattern="leg.statute")

    assert "non-null value" in prompt or "pinpoint" in prompt
    assert "NEVER add a pinpoint" in prompt
