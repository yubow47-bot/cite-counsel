"""Offline tests for Kimi vision API: content array structure, response parsing, error handling."""

import json
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, ".")

from llm_api.kimi_api import (
    extract_from_image,
    extract_from_images,
    _image_to_data_uri,
    _align_fields,
    _build_content,
)

FAKE_KIMI_JSON = json.dumps({
    "page_title": "Supreme Court Rules on Landmark Case",
    "author": "Jane Reporter",
    "date": "2025-06-15",
    "newspaper": "THE GLOBE AND MAIL",
    "url": "",
    "raw_text": "The Supreme Court of Canada today issued its ruling in a landmark case...",
})

EXPECTED_FIELDS = {
    "url": "/fake/path/image.png",
    "page_title": "Supreme Court Rules on Landmark Case",
    "author": "Jane Reporter",
    "date": "2025-06-15",
    "newspaper": "THE GLOBE AND MAIL",
    "hostname": "",
    "style_of_cause": None,
    "neutral_citation": None,
    "statute_title": None,
    "jurisdiction": None,
    "year": "2025",
    "raw_text": "The Supreme Court of Canada today issued its ruling in a landmark case...",
}


# ═══════════════════════════════════════════════════════════════════
#  _build_content
# ═══════════════════════════════════════════════════════════════════

def test_build_content_single_image(tmp_path):
    """Content array for one image has text + one image_url block."""
    img = tmp_path / "test.png"
    img.write_bytes(b"fake-png-bytes")

    content = _build_content([str(img)])

    assert len(content) == 2
    assert content[0]["type"] == "text"
    assert "page_title" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_content_multi_image(tmp_path):
    """Content array for 3 images has text + 3 image_url blocks."""
    paths = []
    for i in range(3):
        p = tmp_path / f"page{i}.png"
        p.write_bytes(b"fake-png-bytes")
        paths.append(str(p))

    content = _build_content(paths)

    assert len(content) == 4  # 1 text + 3 image
    assert all(c["type"] == "image_url" for c in content[1:])


def test_build_content_jpg_mime(tmp_path):
    """JPG files get image/jpeg MIME type."""
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"fake-jpg-bytes")

    content = _build_content([str(img)])

    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


# ═══════════════════════════════════════════════════════════════════
#  _image_to_data_uri
# ═══════════════════════════════════════════════════════════════════

def test_image_to_data_uri_png(tmp_path):
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    uri = _image_to_data_uri(str(img))
    assert uri.startswith("data:image/png;base64,")


def test_image_to_data_uri_jpg(tmp_path):
    img = tmp_path / "test.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")

    uri = _image_to_data_uri(str(img))
    assert uri.startswith("data:image/jpeg;base64,")


# ═══════════════════════════════════════════════════════════════════
#  _align_fields
# ═══════════════════════════════════════════════════════════════════

def test_align_fields_full():
    result = _align_fields({
        "page_title": "Test Title",
        "author": "Test Author",
        "date": "2024-03-01",
        "newspaper": "The Globe and Mail",
        "url": "",
        "raw_text": "Article body text here.",
    }, "/fake/path.png")

    assert result["page_title"] == "Test Title"
    assert result["author"] == "Test Author"
    assert result["date"] == "2024-03-01"
    assert result["newspaper"] == "THE GLOBE AND MAIL"  # uppercased
    assert result["hostname"] == ""
    assert result["year"] == "2024"
    assert result["raw_text"] == "Article body text here."
    assert result["style_of_cause"] is None
    assert result["neutral_citation"] is None
    assert result["url"] == "/fake/path.png"


def test_align_fields_empty_values():
    """Empty fields become None (to match extract_from_url convention)."""
    result = _align_fields({
        "page_title": "",
        "author": "",
        "date": "",
        "newspaper": "",
        "url": "",
        "raw_text": "",
    }, "/fake/path.png")

    assert result["page_title"] is None
    assert result["author"] is None
    assert result["date"] is None
    assert result["newspaper"] is None
    assert result["year"] is None
    assert result["raw_text"] is None


def test_align_fallback_website():
    """No newspaper + has page_title → sets website fallback."""
    result = _align_fields({
        "page_title": "Some Blog Post",
        "author": "",
        "date": "",
        "newspaper": "",
        "url": "",
        "raw_text": "",
    }, "/fake/path.png")

    assert result["newspaper"] is None
    assert result["page_title"] == "Some Blog Post"
    assert "website" in result


# ═══════════════════════════════════════════════════════════════════
#  extract_from_image (mocked HTTP)
# ═══════════════════════════════════════════════════════════════════

@patch("llm_api.kimi_api.kimi_session.post")
def test_extract_from_image_success(mock_post, tmp_path):
    """Happy path: Kimi returns valid JSON → aligned fields returned."""
    img = tmp_path / "test.png"
    img.write_bytes(b"fake-png-bytes")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": FAKE_KIMI_JSON}}]
    }
    mock_post.return_value = mock_resp

    result = extract_from_image(str(img))

    assert "error" not in result
    assert result["page_title"] == EXPECTED_FIELDS["page_title"]
    assert result["author"] == EXPECTED_FIELDS["author"]
    assert result["date"] == EXPECTED_FIELDS["date"]
    assert result["newspaper"] == EXPECTED_FIELDS["newspaper"]
    assert result["year"] == EXPECTED_FIELDS["year"]
    assert result["raw_text"] == EXPECTED_FIELDS["raw_text"]


@patch("llm_api.kimi_api.kimi_session.post")
def test_extract_from_image_api_failure(mock_post, tmp_path):
    """HTTP failure → returns {"error": ...}."""
    img = tmp_path / "test.png"
    img.write_bytes(b"fake")
    mock_post.side_effect = Exception("Connection refused")

    result = extract_from_image(str(img))

    assert "error" in result


@patch("llm_api.kimi_api.kimi_session.post")
def test_extract_from_image_bad_json(mock_post, tmp_path):
    """Bad JSON from API → returns {"error": ...}."""
    img = tmp_path / "test.png"
    img.write_bytes(b"fake")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "Not JSON at all"}}]
    }
    mock_post.return_value = mock_resp

    result = extract_from_image(str(img))

    assert "error" in result


@patch("llm_api.kimi_api.kimi_session.post")
def test_extract_from_image_non_dict_json(mock_post, tmp_path):
    """Valid JSON but not a dict → returns {"error": ...}."""
    img = tmp_path / "test.png"
    img.write_bytes(b"fake")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '["just", "an", "array"]'}}]
    }
    mock_post.return_value = mock_resp

    result = extract_from_image(str(img))

    assert "error" in result


# ═══════════════════════════════════════════════════════════════════
#  extract_from_images (mocked HTTP)
# ═══════════════════════════════════════════════════════════════════

@patch("llm_api.kimi_api.kimi_session.post")
def test_extract_from_images_success(mock_post, tmp_path):
    """Multi-page: all images sent in one call, result aligned."""
    paths = []
    for i in range(3):
        p = tmp_path / f"page{i}.png"
        p.write_bytes(b"fake-png-bytes")
        paths.append(str(p))

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": FAKE_KIMI_JSON}}]
    }
    mock_post.return_value = mock_resp

    result = extract_from_images(paths)

    assert "error" not in result
    assert result["page_title"] == "Supreme Court Rules on Landmark Case"


def test_extract_from_images_empty():
    """Empty list → returns {"error": ...}."""
    result = extract_from_images([])
    assert "error" in result


@patch("llm_api.kimi_api.kimi_session.post")
def test_extract_from_images_kimi_key_not_set(mock_post, tmp_path):
    """When KIMI_API_KEY is missing → raise ValueError (caught by extract_from_image)."""
    img = tmp_path / "test.png"
    img.write_bytes(b"fake")

    with patch("llm_api.kimi_api.os.environ.get", return_value=""):
        result = extract_from_image(str(img))

    assert "error" in result
