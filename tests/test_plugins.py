"""The built-in plugins behind the harness, and the default reply check they share."""
import pytest

from core.grounding import grounded_reply_guard
from harness.core import Harness
from harness.plugin import discover


@pytest.fixture
def h(tmp_path):
    return Harness(discover(), model="m", config_path=tmp_path / "h.json")


def said(session, text):
    session.messages.append({"role": "user", "content": text})


def test_reply_guard_drops_facts_nobody_supplied(h):
    session, _ = h.sessions.start()
    said(session, "R v Gladue")
    ctx = h.context(session, "deadlines")
    assert grounded_reply_guard("这条是 1999 SCC 688。", ctx) == ""
    assert grounded_reply_guard("找到了，请看卡片。", ctx) == "找到了，请看卡片。"


def test_reply_guard_accepts_facts_from_tool_results(h):
    session, _ = h.sessions.start()
    said(session, "R v Gladue")
    session.messages.append({"role": "tool", "tool_call_id": "c1", "content": '{"citation": "[1999] 1 SCR 688"}'})
    assert grounded_reply_guard("见 [1999] 1 SCR 688。", h.context(session, "deadlines")) == "见 [1999] 1 SCR 688。"


def test_deadline_card_lists_every_input(h):
    from plugins import deadlines
    session, _ = h.sessions.start()
    result = deadlines.compute(h.context(session, "deadlines"), deadlines.DeadlineParams(
        start="2026-09-25", days=10, mode="court_days", rule="r 3.02", holidays=["2026-10-12"]))
    rows = dict(result.blocks[0]["rows"])
    assert rows["结果日期"] > "2026-10-08"
    assert rows["节假日"] == "2026-10-12" and result.final is True


def test_web_search_does_nothing_until_a_service_is_chosen(h):
    from plugins.web import SearchParams, search
    session, _ = h.sessions.start()
    result = search(h.context(session, "web"), SearchParams(query="Roncarelli"))
    assert "error" in result.content and result.blocks[0]["type"] == "notice"
