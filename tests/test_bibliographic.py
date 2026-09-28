"""Books and articles found by title are database records, copied field by field."""
import pytest

from core import bibliographic, mcgill_format

OL_SEARCH = {"docs": [
    {"title": "The Early Upanisads", "author_name": ["Patrick Olivelle"],
     "editions": {"docs": [{"key": "/books/OL357760M", "title": "The early Upanisads",
                            "subtitle": "annotated text and translation",
                            "publisher": ["Oxford University Press"], "publish_date": ["1998"]}]}},
    {"title": "Upanisads for Beginners", "author_name": ["Someone Else"],
     "editions": {"docs": [{"key": "/books/OL1M"}]}},
]}
OL_EDITION = {"title": "The early Upanisads", "subtitle": "annotated text and translation",
              "authors": [{"name": "Patrick Olivelle"}], "publishers": [{"name": "Oxford University Press"}],
              "publish_places": [{"name": "New York"}], "publish_date": "1998"}


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


@pytest.mark.parametrize("title,query,expected", [
    ("The Early Upaniṣads", "The Early Upanisads Olivelle", True),
    ("The Concept of Law", "Hart The Concept of Law", True),
    ("Upanisads for Beginners", "The Early Upanisads Olivelle", False),
    ("Law", "", False),
    # Seen live: a long, specific title matched two short generic ones that
    # only share its topic words -- the other direction was never checked.
    ("Indigenous Peoples, Self-determination and International Law",
     "Columbus's Legacy: Law as an Instrument of Racial Discrimination against Indigenous Peoples' "
     "Rights of Self-Determination", False),
    ("Columbus's Legacy: Law as an Instrument of Racial Discrimination against Indigenous Peoples' "
     "Rights of Self-Determination",
     "Williams Columbus's Legacy: Law as an Instrument of Racial Discrimination against Indigenous "
     "Peoples' Rights of Self-Determination 1991", True),
    # A pasted full citation around a short title is still that work...
    ("The Concept of Law", "H.L.A. Hart, The Concept of Law, 3rd ed (Oxford: Oxford University Press, 2012)",
     True),
    ("Columbus's Legacy", "Robert A Williams, Columbus's Legacy (1991) 8 Ariz J Intl & Comp L 51", True),
    # ...but only where the title stands as its own part of the citation,
    ("International Law", "H Smith, Indigenous Peoples, Self-determination and International Law (Oxford: "
                          "Oxford University Press, 2012)", False),
    # and a bare title with a comma in it is not split into generic pieces.
    ("Indigenous Peoples", "Indigenous Peoples, Self-determination and International Law in the Modern Era",
     False),
])
def test_record_title_must_be_what_the_user_typed(title, query, expected):
    assert bibliographic.title_matches(title, query) is expected


def test_book_search_drops_records_the_user_did_not_name(monkeypatch):
    monkeypatch.setattr(bibliographic, "request_with_retry", lambda *a, **k: Response(OL_SEARCH))
    found = bibliographic.search_books("The Early Upanisads Olivelle")
    assert [item["olid"] for item in found] == ["OL357760M"]
    assert found[0]["verified"] is True and "Oxford University Press, 1998" in found[0]["display"]


@pytest.mark.parametrize("title,expected", [
    ("The concept of law", "The Concept of Law"),
    ("The early Upanisads: annotated text and translation", "The Early Upanisads: Annotated Text and Translation"),
    ("Law and the iPhone in the USA", "Law and the iPhone in the USA"),
    ("Self-government of first nations", "Self-Government of First Nations"),
    ("What law is for", "What Law Is For"),
])
def test_english_catalogue_titles_become_title_case(title, expected):
    assert bibliographic.english_title_case(title) == expected


def test_title_case_only_for_english_records():
    french = dict(OL_EDITION, title="Le droit civil", subtitle="", languages=["fre"])
    english = dict(OL_EDITION, title="The concept of law", subtitle="", languages=["eng"])
    assert bibliographic.book_values(french)["title"] == "Le droit civil"
    assert bibliographic.book_values(english)["title"] == "The Concept of Law"


def test_isbn_falls_back_to_the_edition_endpoint(monkeypatch):
    """The Books API now 404s; the edition + author records still resolve."""
    from local_tools import openlibrary_api
    import requests

    class NotFound(Response):
        def raise_for_status(self):
            raise requests.HTTPError("404")
    pages = {"https://openlibrary.org/isbn/9780195124354.json": Response({
                 "title": "The early Upanisads", "authors": [{"key": "/authors/OL838262A"}],
                 "publishers": ["Oxford University Press"], "publish_places": ["New York"], "publish_date": "1998"}),
             "https://openlibrary.org/authors/OL838262A.json": Response({"name": "Patrick Olivelle"})}
    monkeypatch.setattr(openlibrary_api, "request_with_retry",
                        lambda session, method, url, **k: pages.get(url, NotFound(None)))
    data = openlibrary_api.fetch_openlibrary("9780195124354")
    assert bibliographic.book_values(data) == {"author": "Patrick Olivelle", "title": "The early Upanisads",
                                               "edition": "", "place": "New York",
                                               "publisher": "Oxford University Press", "year": "1998"}


@pytest.mark.parametrize("raw,expected", [
    ("RSO 1990, c F3", "RSO 1990, c F.3"), ("R.S.O. 1990, c. F.3", "RSO 1990, c F.3"),
    ("RSC 1985, c C-46", "RSC 1985, c C-46"), ("SO 2019, c 7", "SO 2019, c 7"),
])
def test_statute_chapters(raw, expected):
    assert mcgill_format.mcgill_clean("citation", raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("R. v. Gladue", "R v Gladue"),
    ("Edwards v. Canada (Attorney General)", "Edwards v Canada (AG)"),
])
def test_style_of_cause_follows_mcgill_not_the_database_print(raw, expected):
    assert mcgill_format.mcgill_clean("style_of_cause", raw) == expected


def test_an_author_with_a_suffix_is_not_inverted():
    """Seen live: "Robert A. Williams, Jr" was read as "Last, First" and
    rendered "Jr Robert A. Williams"."""
    from core.mcgill_format import mcgill_clean
    assert mcgill_clean("author", "Robert A. Williams, Jr") == "Robert A. Williams, Jr"
    assert mcgill_clean("author", "Olivelle, Patrick") == "Patrick Olivelle"
