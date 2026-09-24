"""The built-in plugins behind the harness."""
import json

import pytest

from harness.core import Harness
from harness.plugin import discover


@pytest.fixture
def h(tmp_path):
    return Harness(discover(), model="m", config_path=tmp_path / "h.json")


def said(session, text):
    session.messages.append({"role": "user", "content": text})


def test_deadline_card_lists_every_input(h):
    session, _ = h.sessions.start()
    h.set_enabled("deadlines", True)
    session.loaded.append("deadlines")
    call = {"id": "c1", "type": "function", "function": {"name": "deadlines__compute", "arguments": json.dumps({
        "start": "2026-09-25", "days": 10, "mode": "court_days", "rule": "r 3.02", "holidays": ["2026-10-12"]})}}
    result = h._execute(session, call)[1]
    rows = dict(result.blocks[0]["rows"])
    assert rows["Result date"] > "2026-10-08"
    assert rows["Holidays"] == "2026-10-12" and result.final is True


def test_web_search_does_nothing_until_a_service_is_chosen(h):
    from plugins.web import SearchParams, search
    session, _ = h.sessions.start()
    result = search(h.context(session, "web"), SearchParams(query="Roncarelli"))
    assert "error" in result.content and result.blocks[0]["type"] == "notice"
