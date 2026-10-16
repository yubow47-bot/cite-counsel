"""Empty application shells must not become successful evidence records."""
import json

import pytest

from core.tool_contracts import Field, Record
from harness.core import Harness
from plugins import web


# Actual body of rec_4 in the 20261015T210326Z workplace-scope probe.
E_LAWS_SHELL = """e-Laws needs JavaScript to function properly and provide you with a fast, stable experience.
To have a better experience, you need to:
Go to your browser's settings
Enable JavaScript
Le site Lois-en-ligne exige JavaScript pour fonctionner comme il faut, avec rapidité et stabilité.
Pour avoir une meilleure expérience, vous devez :
Accéder aux paramètres de votre navigateur
Activer JavaScript"""


@pytest.fixture
def work(tmp_path):
    harness = Harness({"web": web.PLUGIN}, model="offline", config_path=tmp_path / "h.json")
    harness.set_enabled("web", True)
    session, _ = harness.sessions.start()
    session.loaded.append("web")
    return harness, session


def fetch(work, monkeypatch, text, title="Document"):
    harness, session = work
    def extract(url, capture=None):
        capture(text.encode(), {"final_url": url, "status": 200, "content_type": "text/html"})
        return {"page_title": title, "raw_text": text}
    monkeypatch.setattr("local_tools.web_extract.extract_from_url", extract)
    return harness._execute_once(session, {"function": {"name": "web__fetch", "arguments": json.dumps({
        "url": "https://example.com/law"})}}, {})[1]


@pytest.mark.parametrize("text", [E_LAWS_SHELL, " ".join(E_LAWS_SHELL.split()),
                                 "You need to enable JavaScript to run this app.",
                                 "JavaScript is disabled in your browser. Please enable JavaScript to view this website."])
def test_empty_enablement_page_reports_executed_failure_without_saving_material(work, monkeypatch, text):
    result = fetch(work, monkeypatch, text)
    _, session = work
    assert "requested content was not obtained" in result.content["error"]
    receipt = session.tool_receipts[-1]
    assert receipt["status"] == "error" and receipt["executed"]
    assert receipt["refs"] == [] and list(session.records.all()) == []


@pytest.mark.parametrize("text", [
    "JavaScript enables interactive legal research. This paper evaluates client-side rendering and extraction quality.",
    "Our research application needs JavaScript to function properly.",
    "A frequent message is: You need to enable JavaScript to run this app. The experiment measured its effect on accessibility.",
    "Please enable JavaScript to view this website. Section 1: A notice must identify its recipient and effective date.",
    E_LAWS_SHELL + "\nSection 1: An employer must provide written notice under the applicable statutory conditions.",
])
def test_javascript_articles_and_actual_content_are_not_rejected(work, monkeypatch, text):
    result = fetch(work, monkeypatch, text, title="Research on JavaScript requirements")
    _, session = work
    assert "error" not in result.content
    assert session.tool_receipts[-1]["status"] == "ok"
    assert session.records.get_exact(result.content["record"]["ref"], Record).fields["text"].value == text


def test_failed_shell_refresh_preserves_existing_good_source_version(work, monkeypatch):
    _, session = work
    first = fetch(work, monkeypatch, "Substantive legal text.")
    ref = first.content["record"]["ref"]
    failed = fetch(work, monkeypatch, E_LAWS_SHELL)
    assert "error" in failed.content and session.records.current_ref(ref) == ref
    assert len(list(session.records.all())) == 1
    assert session.records.get_exact(ref).fields["text"] == Field("Substantive legal text.", "extracted")
