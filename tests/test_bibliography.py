"""The citation list becomes a McGill bibliography, rebuilt from store snapshots."""
import pytest

from core import bibliography


def db(value, source="a2aj:x"):
    return {"value": value, "origin": "database", "source_id": source}


def entry(source_type, fields, *, base=None):
    """An entry as the harness record store hands it over (ctx.records.meta(ref))."""
    return {"source_type": source_type, "base": base, "fields": fields}


def sample_entries():
    return [
        entry("book", {"author": db("Patrick Olivelle"), "title": db("The Early Upanisads"),
                       "place": db("New York"), "publisher": db("Oxford University Press"),
                       "year": db("1998"), "pinpoint": {"value": "at 12", "origin": "user"}}),
        entry("jurisprudence", {"style_of_cause": db("R v Gladue"), "reporter": db("[1999] 1 SCR 688")}),
        entry("legislation", {"title": db("Criminal Code"), "citation": db("RSC 1985, c C-46")}),
        entry("jurisprudence", {"style_of_cause": db("Adams v Canada"), "reporter": db("[1996] 3 SCR 101")}),
        entry("journal_article", {"author": db("H. L. A. Hart"),
                                  "title": db("Positivism and the Separation of Law and Morals"),
                                  "year": db("1958"), "volume": db("71"), "issue": db("4"),
                                  "journal": db("Harvard Law Review"), "first_page": db("593")}),
    ]


def test_sections_order_sorting_inversion_and_no_pinpoint():
    result = bibliography.build(sample_entries())
    assert [s["title"] for s in result["sections"]] == ["Legislation", "Jurisprudence", "Secondary Materials"]
    cases = [e["text"] for e in result["sections"][1]["entries"]]
    assert cases == ["*Adams v Canada*, [1996] 3 SCR 101.", "*R v Gladue*, [1999] 1 SCR 688."]
    secondary = [e["text"] for e in result["sections"][2]["entries"]]
    assert secondary == [
        'Hart, H. L. A., "Positivism and the Separation of Law and Morals" (1958) 71:4 *Harvard Law Review* 593.',
        "Olivelle, Patrick, *The Early Upanisads* (New York: Oxford University Press, 1998).",
    ]
    assert "at 12" not in result["text"] and "*" not in result["text"]
    assert result["text"].startswith("LEGISLATION\n\n*".replace("*", "") + "Criminal Code, RSC 1985, c C-46.")


def test_the_pinpoint_does_not_cost_the_entry_its_verification():
    """The footnote is unverified because the user typed a pinpoint; the bibliography omits it."""
    entries = sample_entries()
    result = bibliography.build(entries)
    assert result["count"] == 5
    book = result["sections"][2]["entries"][1]
    assert book["verified"] is True and result["unverified"] == 0 and result["grounded"] is True


def test_any_other_user_field_still_makes_the_entry_unverified():
    edited = entry("book", {"author": db("Patrick Olivelle"), "title": db("The Early Upanisads"),
                            "place": {"value": "Delhi", "origin": "user"}, "publisher": db("OUP"),
                            "year": db("1998")})
    result = bibliography.build([edited])
    assert result["unverified"] == 1 and result["grounded"] is False


def test_same_author_sorts_by_title_without_articles():
    hart_book = entry("book", {"author": db("H. L. A. Hart"), "title": db("The Concept of Law"),
                               "place": db("Oxford"), "publisher": db("Clarendon Press"), "year": db("1961")})
    result = bibliography.build(sample_entries() + [hart_book])
    texts = [e["text"] for e in result["sections"][2]["entries"]]
    assert texts[0].startswith("Hart, H. L. A., *The Concept of Law*") and texts[1].startswith('Hart, H. L. A., "Positivism')


def test_duplicates_collapse():
    first = sample_entries()
    result = bibliography.build(first + first[1:3])
    assert result["count"] == 5


@pytest.mark.parametrize("author,expected", [
    ("Patrick Olivelle", "Olivelle, Patrick"),
    ("John Smith, William Jones & Timothy Adams", "Smith, John, William Jones & Timothy Adams"),
    ("John Smith et al", "Smith, John et al"),
    ("Canadian Bar Association", "Canadian Bar Association"),
    ("Plato", "Plato"),
])
def test_only_the_first_author_is_inverted(author, expected):
    assert bibliography.invert_first_author(author) == expected


def test_build_returns_the_bibliography_artifact():
    result = bibliography.build(sample_entries())
    assert result["artifact"].kind == "bibliography"
    assert result["artifact"].content == result["text"]


def test_bad_entries_are_refused_at_the_boundary():
    """No signature exists any more: the store owns entries. Malformed input
    still fails closed at the build itself."""
    with pytest.raises(ValueError):
        bibliography.build([])
    with pytest.raises(ValueError):
        bibliography.build([{"source_type": "book", "base": None, "fields": "not a mapping"}])


def test_legacy_strings_are_listed_as_issued():
    result = bibliography.build([entry("legislation", {}, base="*Canadian Charter of Rights and Freedoms*, "
                                                              "s 7, Part I of the *Constitution Act, 1982*.")])
    assert result["sections"][0]["entries"][0]["text"].startswith("*Canadian Charter")
    assert result["grounded"] is False