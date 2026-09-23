"""The extract plugins: file uploads and web pages become extracted records."""
from unittest.mock import patch

import pytest

from core.tool_contracts import Record
from harness.core import Harness
from harness.plugin import discover
from harness.session import Context


def make():
    return Harness(discover(), model="m")


def ctx_for(h, session, name):
    return Context(session, name, h)


def attach(session, tmp_path, attachment_id="att_1a2b3c4d", name="judgment.pdf"):
    path = tmp_path / attachment_id
    path.write_bytes(b"%PDF-1.4 dummy")
    session.attachments[attachment_id] = {"path": str(path), "name": name}


def test_file_extract_stores_an_extracted_record(tmp_path):
    h = make()
    session, _ = h.sessions.start()
    attach(session, tmp_path=tmp_path)
    from plugins import file
    with patch("local_tools.file_extractor.extract_from_file", return_value={
        "title": "A Judgment", "author": "", "date": "2024-01-05", "publisher": "", "raw_text": "some text",
    }):
        result = file.extract(ctx_for(h, session, "file"), file.ExtractParams(attachment_id="att_1a2b3c4d"))
    record = session.records.get(result.content["ref"], Record)
    assert record.source_type == "document"
    assert record.provider == "file" and record.record_id == "att_1a2b3c4d"
    assert all(f.origin == "extracted" for f in record.fields.values())


def test_file_extract_refuses_an_empty_file(tmp_path):
    h = make()
    session, _ = h.sessions.start()
    attach(session, tmp_path=tmp_path)
    from plugins import file
    with patch("local_tools.file_extractor.extract_from_file", return_value={"title": "", "raw_text": ""}):
        with pytest.raises(ValueError):
            file.extract(ctx_for(h, session, "file"), file.ExtractParams(attachment_id="att_1a2b3c4d"))


def test_file_extract_needs_a_real_attachment():
    h = make()
    session, _ = h.sessions.start()
    from plugins import file
    with pytest.raises(ValueError):
        file.extract(ctx_for(h, session, "file"), file.ExtractParams(attachment_id="att_missing"))


def test_web_fetch_stores_the_page_as_an_extracted_record():
    h = make()
    session, _ = h.sessions.start()
    from plugins import web
    with patch("llm_api.deepseek_api.extract_from_url", return_value={
        "page_title": "A page", "raw_text": "page body text",
    }):
        result = web.fetch(ctx_for(h, session, "web"),
                           web.FetchParams(url="https://example.com/page"))
    record = session.records.get(result.content["record"]["ref"], Record)
    assert record.source_type == "website" and record.record_id == "https://example.com/page"
    assert all(f.origin == "extracted" for f in record.fields.values())
    assert result.content["text"] == "page body text"          # the model still reads the page