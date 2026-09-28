"""The upload magic-byte gate moved into harness.app with the rest of the
upload path. These are direct tests of the pure function."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.app import _magic_byte_ok


def test_pdf_header():
    assert _magic_byte_ok(b"%PDF-1.7 rest", ".pdf")
    assert not _magic_byte_ok(b"NOTAPDF.........", ".pdf")


def test_ooxml_zip_containers():
    for sig in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        assert _magic_byte_ok(sig + b"....", ".docx")
        assert _magic_byte_ok(sig + b"....", ".pptx")
        assert _magic_byte_ok(sig + b"....", ".xlsx")
    assert not _magic_byte_ok(b"MZ\x90\x00....", ".docx")  # a PE binary is not a zip


def test_image_headers():
    assert _magic_byte_ok(b"\xff\xd8\xff\xe0....", ".jpg")
    assert _magic_byte_ok(b"\xff\xd8\xff\xe0....", ".jpeg")
    assert _magic_byte_ok(b"\x89PNG\r\n\x1a\n....", ".png")
    assert _magic_byte_ok(b"RIFF....WEBPVP8 ", ".webp")
    assert not _magic_byte_ok(b"GIF89a........", ".png")
    assert not _magic_byte_ok(b"RIFF....WAVEfmt ", ".webp")


def test_empty_head_and_unknown_suffix_are_rejected():
    assert not _magic_byte_ok(b"", ".pdf")
    assert not _magic_byte_ok(b"%PDF-1.7 rest", ".exe")
