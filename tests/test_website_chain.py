"""Tests for the website/news chain unification (Commit B).

The URL path and the screenshot path must produce the same site-name shape:
a real publication name (screenshots, Gemini vision) stays verbatim; the URL
path yields a lowercase bare domain (site_domain) — never a fabricated
uppercase "newspaper".

Run: pytest tests/test_website_chain.py -v
"""

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mcgill_engine import build_prompt, detect_type
from llm_api.deepseek_api import extract_from_url
from llm_api.gemini_api import _align_fields


# ═════════════════════════════════════════════════════════════════════════════
#  URL path — site_domain, no fabricated newspaper
# ═════════════════════════════════════════════════════════════════════════════

_FAKE_META = json.dumps({
    "title": "The Midas Conundrum",
    "author": "Richard Gold",
    "date": "2017-04-25",
    "hostname": "cigionline.org",
    "raw_text": "Article body " * 20,
})


def test_extract_from_url_emits_lowercase_site_domain():
    with patch("llm_api.deepseek_api.fetch_html", return_value="<html>ok</html>"), \
         patch("trafilatura.extract", return_value=_FAKE_META), \
         patch("llm_api.deepseek_api.parse_llm_json",
               return_value=json.loads(_FAKE_META)):
        fields = extract_from_url("https://cigionline.org/articles/midas")

    assert fields["site_domain"] == "cigionline.org"      # lowercase bare domain
    assert "newspaper" not in fields                      # no fabricated name
    assert fields["date"] == "2017-04-25"                 # raw ISO; humanized only in prompt


def test_url_fields_no_longer_classified_as_news_sources():
    """detect_type previously hit the newspaper branch on the fabricated
    newspaper field; the URL shape now falls through to websites."""
    fields = {
        "url": "https://cigionline.org/x",
        "page_title": "T",
        "site_domain": "cigionline.org",
        "hostname": "cigionline.org",
        "raw_text": "body text",
    }
    assert detect_type(fields) == "secondary_sources.websites"


# ═════════════════════════════════════════════════════════════════════════════
#  Screenshot path — real publication names stay verbatim
# ═════════════════════════════════════════════════════════════════════════════

def test_gemini_keeps_real_publication_name():
    fields = _align_fields({
        "page_title": "Some Story",
        "newspaper": "The Globe and Mail",
        "date": "2023-06-01",
        "raw_text": "story body",
    })
    assert fields["newspaper"] == "The Globe and Mail"    # NOT upper-cased


# ═════════════════════════════════════════════════════════════════════════════
#  STRICT block — web-source line only for web types
# ═════════════════════════════════════════════════════════════════════════════

_RULES = {"category": "X", "topics": []}


def test_strict_web_line_present_for_web_types():
    for dt in ("secondary_sources.websites", "secondary_sources.news_sources"):
        prompt = build_prompt({"page_title": "T", "site_domain": "example.com"}, dt, _RULES)
        assert "For web sources" in prompt, dt


def test_strict_web_line_absent_for_other_types():
    for dt in ("jurisprudence", "legislation",
               "secondary_sources.journal_articles", "secondary_sources.books"):
        prompt = build_prompt({"style_of_cause": "X"}, dt, _RULES)
        assert "For web sources" not in prompt and "For websites:" not in prompt, dt


def test_prompt_uses_unified_web_template():
    rules = {
        "category": "Websites",
        "topics": [{
            "topic": "Websites",
            "template": 'Author, | "title of the page/article" | (date of the page/article) | pinpoint, | online: | <site domain> | [archived URL].',
            "examples": ['A, "T" (25 April 2017) online: <example.com> [perma.cc/1].'],
        }],
    }
    prompt = build_prompt({"page_title": "T", "site_domain": "example.com",
                           "date": "2017-04-25"},
                          "secondary_sources.websites", rules)
    assert "online: <site domain>" in prompt            # unified template survives pipe-strip
    assert "(type of electronic source)" not in prompt  # malformed variant gone
