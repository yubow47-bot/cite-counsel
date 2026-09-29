"""Tests for the website/news chain unification (Commit B).

The URL path and the screenshot path must produce the same site-name shape:
a real publication name (screenshots, vision model) stays verbatim; the URL
path yields a lowercase bare domain (site_domain) — never a fabricated
uppercase "newspaper".

Run: pytest tests/test_website_chain.py -v
"""

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools.web_extract import extract_from_url
from llm_api.vision import _align_fields


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
    with patch("local_tools.web_extract.fetch_html", return_value="<html>ok</html>"), \
         patch("trafilatura.extract", return_value=_FAKE_META), \
         patch("local_tools.web_extract.parse_llm_json",
               return_value=json.loads(_FAKE_META)):
        fields = extract_from_url("https://cigionline.org/articles/midas")

    assert fields["site_domain"] == "cigionline.org"      # lowercase bare domain
    assert "newspaper" not in fields                      # no fabricated name
    assert fields["date"] == "2017-04-25"                 # raw ISO; humanized only in prompt


# ═════════════════════════════════════════════════════════════════════════════
#  Screenshot path — real publication names stay verbatim
# ═════════════════════════════════════════════════════════════════════════════

def test_vision_keeps_real_publication_name():
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

