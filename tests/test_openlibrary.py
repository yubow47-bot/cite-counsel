"""Offline unit tests for openlibrary_api: ISBN extraction and validation."""

import sys
sys.path.insert(0, ".")

from local_tools.openlibrary_api import (
    extract_isbn,
    validate_isbn,
)


# ═══════════════════════════════════════════════════════════════════
#  validate_isbn
# ═══════════════════════════════════════════════════════════════════

def test_valid_isbn13():
    """9780804429573 is a valid ISBN-13 (check digit 3)."""
    assert validate_isbn("9780804429573") is True


def test_valid_isbn13_with_hyphens():
    """validate_isbn should strip hyphens before checking."""
    assert validate_isbn("978-0-8044-2957-3") is True


def test_invalid_isbn13_wrong_check():
    """One wrong digit → fails checksum (9780804429573 → 3 is check, test with 4)."""
    assert validate_isbn("9780804429574") is False


def test_invalid_isbn13_not_978_979():
    """Does not start with 978/979 → fails length check first."""
    assert validate_isbn("1234567890123") is False


def test_valid_isbn10():
    """080442957X is a valid ISBN-10 (check digit X)."""
    assert validate_isbn("080442957X") is True


def test_valid_isbn10_with_x():
    """ISBN-10 with check digit X."""
    assert validate_isbn("080442957X") is True


def test_valid_isbn10_lowercase_x():
    """Lowercase x check digit."""
    assert validate_isbn("080442957x") is True


def test_invalid_isbn10_wrong_check():
    assert validate_isbn("0804429571") is False


def test_invalid_isbn10_too_short():
    assert validate_isbn("123456789") is False


def test_invalid_isbn10_too_long():
    assert validate_isbn("12345678901") is False


def test_validate_none_empty():
    assert validate_isbn("") is False
    assert validate_isbn(None) is False  # type: ignore


# ═══════════════════════════════════════════════════════════════════
#  extract_isbn
# ═══════════════════════════════════════════════════════════════════

def test_extract_isbn13_bare():
    assert extract_isbn("9780804429573") == "9780804429573"


def test_extract_isbn13_with_hyphens():
    assert extract_isbn("978-0-8044-2957-3") == "9780804429573"


def test_extract_isbn13_isbn_prefix():
    assert extract_isbn("ISBN: 978-0-8044-2957-3") == "9780804429573"


def test_extract_isbn13_isbn_prefix_no_colon():
    assert extract_isbn("ISBN 978-0-8044-2957-3") == "9780804429573"


def test_extract_isbn10_bare():
    assert extract_isbn("080442957X") == "080442957X"


def test_extract_isbn10_with_hyphens():
    assert extract_isbn("0-8044-2957-X") == "080442957X"


def test_extract_isbn10_isbn_prefix():
    assert extract_isbn("ISBN: 0-8044-2957-X") == "080442957X"


def test_extract_isbn10_isbn10_prefix():
    assert extract_isbn("ISBN-10: 0-8044-2957-X") == "080442957X"


def test_extract_isbn13_isbn13_prefix():
    assert extract_isbn("ISBN-13: 978-0-8044-2957-3") == "9780804429573"


def test_extract_isbn13_prefers_isbn13():
    """When both ISBN-13 and ISBN-10 appear, ISBN-13 wins."""
    assert extract_isbn("ISBN-10: 080442957X  ISBN-13: 9780804429573") == "9780804429573"


def test_extract_isbn_none_on_empty():
    assert extract_isbn("") is None
    assert extract_isbn(None) is None  # type: ignore


def test_extract_isbn_no_isbn_in_text():
    assert extract_isbn("This is just some text about law") is None


# ═══════════════════════════════════════════════════════════════════
#  build_book_citation (minimal — relies on dict shape, no HTTP)
# ═══════════════════════════════════════════════════════════════════

_SAMPLE_OL_DATA = {
    "title": "Telecommunications Law",
    "authors": [{"name": "David Gilles"}],
    "publishers": [{"name": "Butterworths"}],
    "publish_date": "2003",
    "publish_places": [{"name": "London, UK"}],
}

_SAMPLE_OL_DATA_NO_PLACE = {
    "title": "Telecommunications Law",
    "authors": [{"name": "David Gilles"}],
    "publishers": [{"name": "Butterworths"}],
    "publish_date": "2003",
}

