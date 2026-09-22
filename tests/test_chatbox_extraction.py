"""Offline tests: model copies fields, code renders; invented values are dropped."""

import json

import pytest

from core import chatbox_service as service
from core.decisions import jev_client
from core.decisions.models import DecisionResult

ARTICLE = """Osgoode Hall Law Journal
Volume 45, Number 1 (2008)
The Duty to Consult: New Directions
Dwight Newman
pages 1-38
"""


def test_journal_article_template_matches_mcgill_example():
    assert service.render_fields("journal_article", {
        "author": "Dwight Newman", "title": "The Duty to Consult: New Directions", "year": "2008",
        "volume": "45", "issue": "1", "journal": "Osgoode Hall LJ", "first_page": "1", "pinpoint": "23",
    }) == 'Dwight Newman, "The Duty to Consult: New Directions" (2008) 45:1 *Osgoode Hall LJ* 1 at 23.'


def test_newspaper_template_matches_rules_examples():
    assert service.render_fields("newspaper", {
        "title": "Ruling on Baby with Three Mothers", "newspaper": "BBC News",
        "date": "10 November 2005", "site": "news.bbc.co.uk",
    }) == '"Ruling on Baby with Three Mothers", BBC News (10 November 2005), online: <news.bbc.co.uk>.'
    assert service.render_fields("newspaper", {
        "author": "Bill Curry", "title": "PM, Premiers Work Out Deal", "newspaper": "The Globe and Mail",
        "date": "26 November 2005", "page": "A4",
    }) == 'Bill Curry, "PM, Premiers Work Out Deal", The Globe and Mail (26 November 2005) A4.'


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = []

    def install(answer):
        import llm_api.deepseek_api as ds
        def ask(prompt, **kwargs):
            calls.append(prompt)
            return json.dumps(answer)
        monkeypatch.setattr(ds, "ask_deepseek", ask)
        return calls
    return install


def test_values_not_in_source_are_dropped(fake_llm):
    fake_llm({"author": "Invented Person", "title": "The Duty to Consult: New Directions",
              "year": "2008", "volume": 45, "issue": "1", "journal": "Osgoode Hall Law Journal", "first_page": "1"})
    fields = service.extract_fields("journal_article", ARTICLE)
    assert "author" not in fields
    assert fields["volume"] == "45" and fields["journal"] == "Osgoode Hall Law Journal"


def test_statute_citation_cleaned_to_mcgill_form(fake_llm):
    fake_llm({"title": "Criminal Code", "citation": "R.S.C., 1985, c. C-46"})
    fields = service.extract_fields("legislation", "Criminal Code\nR.S.C., 1985, c. C-46\nAn Act respecting")
    assert fields == {"title": "Criminal Code", "citation": "RSC 1985, c C-46"}


def _jev(monkeypatch, choice, confidence=0.9):
    probs = {key: 0.0 for key in service.DOCUMENT_TYPES}
    probs[choice] = 1.0

    class Stub:
        def __init__(self, *a, **k): pass
        def decide_choice(self, state, question):
            return DecisionResult(question.question_id, choice, probs, confidence, "jev-latest", 90.0)
        def close(self): pass
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(jev_client, "JevClient", Stub)


def test_file_is_classified_by_jev_and_rendered_by_code(monkeypatch, tmp_path, fake_llm):
    monkeypatch.setattr(service, "_ROUTE_LOG", tmp_path / "log.jsonl")
    _jev(monkeypatch, "journal_article")
    monkeypatch.setattr("local_tools.file_extractor.classify_document_type", lambda _: pytest.fail("LLM classifier called"))
    fake_llm({"author": "Dwight Newman", "title": "The Duty to Consult: New Directions", "year": "2008",
              "volume": "45", "issue": "1", "journal": "Osgoode Hall Law Journal", "first_page": "1"})
    blocks = service.extracted_blocks({"raw_text": ARTICLE}, False)
    assert blocks[0]["shadow"] is False
    result = next(b for b in blocks if b["type"] == "citation_result")
    assert result["citation"] == ('Dwight Newman, "The Duty to Consult: New Directions" (2008) 45:1 '
                                  '*Osgoode Hall Law Journal* 1.')
    assert result["verified"] is False
    assert "Osgoode Hall Law Journal" in blocks[-1]["message"]  # excerpt kept for checking


def test_shadow_mode_does_not_adopt_jev(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_ROUTE_LOG", tmp_path / "log.jsonl")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _jev(monkeypatch, "journal_article")
    monkeypatch.setattr("local_tools.file_extractor.classify_document_type", lambda _: "book")
    calls = []
    monkeypatch.setattr("core.mcgill_engine.format_citation", lambda fields, doc_type=None: calls.append(doc_type) or "")
    blocks = service.extracted_blocks({"raw_text": ARTICLE}, True)
    assert blocks[0]["shadow"] is True
    # The fallback classifier (book) wins. With no book fields the user is asked
    # for them; the free-form formatter is never reached.
    assert calls == [] and "citation_result" not in [b["type"] for b in blocks]
    question = next(b for b in blocks if b["type"] == "field_question")
    assert question["source_type"] == "book"
    assert [f["name"] for f in question["fields"]] == ["author", "title", "place", "publisher", "year"]


def test_webpage_uses_page_metadata_without_llm(monkeypatch, tmp_path, fake_llm):
    monkeypatch.setattr(service, "_ROUTE_LOG", tmp_path / "log.jsonl")
    _jev(monkeypatch, "website")
    calls = fake_llm({})
    blocks = service.extracted_blocks({
        "url": "https://example.org/post", "page_title": "Housing and the Charter",
        "author": "Jane Roe", "date": "2024-03-05", "raw_text": "Housing and the Charter. " + "Body text. " * 10,
    }, False, webpage=True)
    result = next(b for b in blocks if b["type"] == "citation_result")
    assert result["citation"] == 'Jane Roe, "Housing and the Charter" (5 March 2024), online: <https://example.org/post>.'
    assert calls == []


YAHOO_BODY = "On Holding's (ONON) entry into the soccer category ... " * 3

YAHOO = {
    "url": "https://finance.yahoo.com/markets/stocks/article/on-holdings-193202945.html",
    "page_title": "On Holding's Kylian Mbappé bet 'a direct challenge to Nike and Adidas': Analyst",
    "site_name": "Yahoo Finance", "author": "Brooke DiPalma", "date": "2026-09-18",
    "raw_text": YAHOO_BODY,
}


def test_online_news_uses_outlet_name_and_full_host(monkeypatch, tmp_path, fake_llm):
    monkeypatch.setattr(service, "_ROUTE_LOG", tmp_path / "log.jsonl")
    _jev(monkeypatch, "newspaper")
    calls = fake_llm({})
    blocks = service.extracted_blocks(YAHOO, False, webpage=True)
    result = next(b for b in blocks if b["type"] == "citation_result")
    assert result["citation"] == ("Brooke DiPalma, \"On Holding's Kylian Mbappé bet 'a direct challenge to Nike and "
                                  "Adidas': Analyst\", Yahoo Finance (18 September 2026), online: <finance.yahoo.com>.")
    assert calls == []


def test_online_news_without_outlet_falls_back_to_web_page(monkeypatch, tmp_path, fake_llm):
    monkeypatch.setattr(service, "_ROUTE_LOG", tmp_path / "log.jsonl")
    _jev(monkeypatch, "newspaper")
    fake_llm({"page": ""})
    blocks = service.extracted_blocks({**YAHOO, "site_name": ""}, False, webpage=True)
    result = next(b for b in blocks if b["type"] == "citation_result")
    assert result["source_type"] == "website"
    assert result["citation"].endswith("(18 September 2026), online: <https://finance.yahoo.com/markets/stocks/article/on-holdings-193202945.html>.")


def test_missing_required_fields_become_a_question(monkeypatch, tmp_path, fake_llm):
    """The gap is asked about, not papered over by the free-form formatter."""
    monkeypatch.setattr(service, "_ROUTE_LOG", tmp_path / "log.jsonl")
    _jev(monkeypatch, "journal_article")
    fake_llm({"title": "The Duty to Consult: New Directions"})
    monkeypatch.setattr("core.mcgill_engine.format_citation",
                        lambda fields, doc_type=None: pytest.fail("free-form formatter reached"))
    blocks = service.extracted_blocks({"raw_text": ARTICLE}, False)
    assert "citation_result" not in [b["type"] for b in blocks]
    question = next(b for b in blocks if b["type"] == "field_question")
    assert [f["name"] for f in question["fields"]] == ["year", "journal", "first_page"]
    # What the model did copy out of the source is shown, tagged as extracted.
    assert question["known"] == [{"name": "title", "label": question["known"][0]["label"], "required": True,
                                 "value": "The Duty to Consult: New Directions", "origin": "extracted",
                                 "origin_label": service.ORIGIN_LABELS["extracted"]}]


def test_answering_a_question_renders_the_citation_unverified(monkeypatch, tmp_path, fake_llm):
    monkeypatch.setattr(service, "_ROUTE_LOG", tmp_path / "log.jsonl")
    _jev(monkeypatch, "journal_article")
    fake_llm({"title": "The Duty to Consult: New Directions"})
    question = next(b for b in service.extracted_blocks({"raw_text": ARTICLE}, False)
                    if b["type"] == "field_question")
    item = service.item_store.update(question["item_id"], question["access_token"], question["revision"],
                                     {"year": "2008", "journal": "Osgoode Hall LJ", "first_page": "1"})
    result = service.item_blocks(question["item_id"], question["access_token"], item)[0]
    assert result["type"] == "citation_result" and result["verified"] is False
    assert result["citation"] == ('"The Duty to Consult: New Directions" (2008) '
                                  '*Osgoode Hall LJ* 1.')
    assert result["revision"] == 2


@pytest.mark.parametrize("meta_title, html, expected", [
    ("WhOn Holding's bet", "<title>On Holding's bet</title>", "On Holding's bet"),
    ("Housing and the Charter", "<title>Housing and the Charter | CBC News</title>", "Housing and the Charter"),
    ("", "<title>Only &amp; Title</title>", "Only & Title"),
    ("Different", "<title>Unrelated</title>", "Different"),
])
def test_best_title_prefers_clean_article_title(meta_title, html, expected):
    from llm_api.deepseek_api import _best_title
    assert _best_title(meta_title, html) == expected


def test_meta_content_reads_site_name():
    from llm_api.deepseek_api import _meta_content
    html = '<head><meta property="og:site_name" content="Yahoo &amp; Finance"/></head>'
    assert _meta_content(html, "og:site_name") == "Yahoo & Finance"


SOURCE_NOTE_DOC = ("A Selection of Passages\n" + "Scripture passage text. " * 400 +
                   "\nAll passages are from the following source:\n"
                   "Olivielle, Patrick. The Early Upanisads: Annotated Text and Translation. "
                   "New York: Oxford University Press, 1998.")


def test_head_tail_keeps_source_note_at_end():
    from local_tools.file_extractor import head_tail
    kept = head_tail(SOURCE_NOTE_DOC, 3000, 1500)
    assert kept.startswith("A Selection of Passages") and "Oxford University Press, 1998" in kept
    assert head_tail("short", 10, 10) == "short"


def test_book_from_trailing_source_note(monkeypatch, tmp_path, fake_llm):
    from local_tools.file_extractor import head_tail
    monkeypatch.setattr(service, "_ROUTE_LOG", tmp_path / "log.jsonl")
    seen = []
    monkeypatch.setattr("local_tools.file_extractor.classify_document_type", lambda t: seen.append(t) or "book")
    fake_llm({"author": "Olivielle, Patrick", "title": "The Early Upanisads: Annotated Text and Translation.",
              "edition": "", "place": "New York", "publisher": "Oxford University Press", "year": "1998"})
    blocks = service.extracted_blocks({"raw_text": head_tail(SOURCE_NOTE_DOC, 3000, 1500)}, False)
    assert "Oxford" in seen[0][:800]  # the classifier sees the source note
    result = next(b for b in blocks if b["type"] == "citation_result")
    assert result["citation"] == ("Patrick Olivielle, *The Early Upanisads: Annotated Text and Translation* "
                                  "(New York: Oxford University Press, 1998).")


@pytest.mark.parametrize("raw, expected", [
    ("Olivelle, Patrick", "Patrick Olivelle"),
    ("Patrick Olivelle", "Patrick Olivelle"),
    ("Smith, John & Jane Doe", "Smith, John & Jane Doe"),
    ("Newman, Dwight, et al", "Newman, Dwight, et al"),
])
def test_author_bibliography_order_flipped_only_for_single_author(raw, expected):
    assert service._mcgill_clean("author", raw) == expected
