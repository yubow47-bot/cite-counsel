"""Tests for file_extractor: PDF scanned fallback, image dispatch."""

import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, ".")

from local_tools.file_extractor import (
    extract_from_file,
    _extract_pdf,
    SCANNED_THRESHOLD,
)


# ═══════════════════════════════════════════════════════════════════
#  Image dispatch
# ═══════════════════════════════════════════════════════════════════

@patch("llm_api.vision.extract_from_image")
def test_extract_from_file_png(mock_extract):
    """extract_from_file with .png → calls extract_from_image."""
    mock_extract.return_value = {"page_title": "Test"}
    result = extract_from_file("/fake/image.png")
    assert result["page_title"] == "Test"
    mock_extract.assert_called_once_with("/fake/image.png")


@patch("llm_api.vision.extract_from_image")
def test_extract_from_file_jpg(mock_extract):
    """extract_from_file with .jpg → calls extract_from_image."""
    mock_extract.return_value = {"page_title": "Test"}
    result = extract_from_file("/fake/photo.jpg")
    assert result["page_title"] == "Test"
    mock_extract.assert_called_once_with("/fake/photo.jpg")


@patch("llm_api.vision.extract_from_image")
def test_extract_from_file_jpeg(mock_extract):
    """extract_from_file with .jpeg → calls extract_from_image."""
    mock_extract.return_value = {"page_title": "Test"}
    result = extract_from_file("/fake/photo.jpeg")
    assert result["page_title"] == "Test"
    mock_extract.assert_called_once_with("/fake/photo.jpeg")


@patch("llm_api.vision.extract_from_image")
def test_extract_from_file_webp(mock_extract):
    """extract_from_file with .webp → calls extract_from_image."""
    mock_extract.return_value = {"page_title": "Test"}
    result = extract_from_file("/fake/image.webp")
    assert result["page_title"] == "Test"
    mock_extract.assert_called_once_with("/fake/image.webp")


def test_extract_from_file_unsupported():
    """Unsupported extension returns raw_input with message."""
    result = extract_from_file("/fake/file.xyz")
    assert "raw_input" in result
    assert "Unsupported" in result["raw_input"]


# ═══════════════════════════════════════════════════════════════════
#  PDF — scanned vs text dispatch
# ═══════════════════════════════════════════════════════════════════

def _mock_pdf_page(text: str) -> MagicMock:
    """Create a mock pdfplumber page with extract_text returning given text."""
    page = MagicMock()
    page.extract_text.return_value = text
    return page


def _mock_pdfplumber_open(pages_text: list[str]) -> MagicMock:
    """Create a mock pdfplumber.open context manager yielding pages."""
    pages = [_mock_pdf_page(t) for t in pages_text]
    pdf = MagicMock()
    pdf.pages = pages
    # Make it a context manager
    ctx = MagicMock()
    ctx.__enter__.return_value = pdf
    ctx.__exit__.return_value = None
    return ctx


@patch("pdfplumber.open")
@patch("local_tools.file_extractor._extract_pdf_scanned")
def test_pdf_scanned_fallback(mock_scanned, mock_open):
    """PDF with very little text (< SCANNED_THRESHOLD) → _extract_pdf_scanned."""
    mock_open.return_value = _mock_pdfplumber_open(["   "])

    _extract_pdf("/fake/scanned.pdf")

    mock_scanned.assert_called_once_with("/fake/scanned.pdf")


@patch("pdfplumber.open")
@patch("local_tools.file_extractor._extract_pdf_scanned")
def test_pdf_text_pipeline(mock_scanned, mock_open):
    """PDF with enough text → text pipeline (not scanned)."""
    mock_open.return_value = _mock_pdfplumber_open(
        ["A" * (SCANNED_THRESHOLD + 10)]
    )

    result = _extract_pdf("/fake/text.pdf")

    assert mock_scanned.call_count == 0  # scanned path NOT called
    assert "raw_text" in result
    assert "title" in result


@patch("pdfplumber.open")
@patch("local_tools.file_extractor._extract_pdf_scanned")
def test_pdf_exactly_at_threshold(mock_scanned, mock_open):
    """PDF with exactly SCANNED_THRESHOLD chars of text → text pipeline."""
    mock_open.return_value = _mock_pdfplumber_open(["x" * SCANNED_THRESHOLD])

    result = _extract_pdf("/fake/boundary.pdf")

    assert mock_scanned.call_count == 0
    assert "raw_text" in result


@patch("pdfplumber.open")
@patch("local_tools.file_extractor._extract_pdf_scanned")
def test_pdf_one_below_threshold(mock_scanned, mock_open):
    """PDF with SCANNED_THRESHOLD - 1 chars → scanned path."""
    mock_open.return_value = _mock_pdfplumber_open(["x" * (SCANNED_THRESHOLD - 1)])

    _extract_pdf("/fake/scanned2.pdf")

    mock_scanned.assert_called_once()
