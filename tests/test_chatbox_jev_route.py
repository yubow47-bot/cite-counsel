"""Offline tests for the JEV fast query route in the Chatbox."""

import pytest

from core import chatbox_service as service
from core.decisions import jev_client
from core.decisions.client import DecisionError
from core.decisions.models import DecisionResult


class _StubJev:
    def __init__(self, result=None, error=False):
        self.result, self.error, self.calls = result, error, 0

    def __call__(self, *args, **kwargs):
        return self

    def decide_choice(self, state, question):
        self.calls += 1
        if self.error:
            raise DecisionError("boom")
        assert set(question.criteria) == set(service.QUERY_ROUTES)
        return self.result

    def close(self):
        pass


def _result(choice, confidence):
    probs = {key: 0.0 for key in service.QUERY_ROUTES}
    probs[choice] = 1.0
    return DecisionResult("query_route.v1", choice, probs, confidence, "jev-latest", 120.0)


@pytest.fixture
def jev(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(service, "_ROUTE_LOG", tmp_path / "jev_routes.jsonl")

    def install(stub):
        monkeypatch.setattr(jev_client, "JevClient", stub)
        return stub
    return install


def _forbid_llm(monkeypatch):
    import local_tools.citation_search as cs
    monkeypatch.setattr(cs, "classify_and_normalize", lambda q: pytest.fail("LLM classifier called"))


def test_confident_jev_route_skips_llm(jev, monkeypatch):
    jev(_StubJev(_result("legislation", 0.93)))
    _forbid_llm(monkeypatch)
    assert service.classify_query("Criminal Code")["type"] == "legislation"
    assert service._ROUTE_LOG.read_text(encoding="utf-8").count('"adopted": true') == 1


@pytest.mark.parametrize("stub", [_StubJev(_result("concept", 0.3)), _StubJev(error=True)])
def test_unsure_or_failed_jev_falls_back_to_llm(jev, monkeypatch, stub):
    jev(stub)
    import local_tools.citation_search as cs
    monkeypatch.setattr(cs, "classify_and_normalize", lambda q: {"type": "case_name"})
    assert service.classify_query("Sharma")["type"] == "case_name"
    assert stub.calls == 1


def test_no_key_never_calls_jev(jev, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    stub = jev(_StubJev(_result("concept", 0.99)))
    import local_tools.citation_search as cs
    monkeypatch.setattr(cs, "classify_and_normalize", lambda q: {"type": "concept"})
    assert service.classify_query("duty to consult")["type"] == "concept"
    assert stub.calls == 0


def test_regex_fast_paths_need_no_model(jev, monkeypatch):
    stub = jev(_StubJev(_result("concept", 0.99)))
    _forbid_llm(monkeypatch)
    assert service.classify_query("Bill C-22")["type"] == "bill"
    assert service.classify_query("R v Gladue")["type"] == "case_name"
    assert stub.calls == 0


@pytest.mark.parametrize("text, expected", [
    ("bill 4", "C-4"), ("Bill C-4", "C-4"), ("bill c 34", "C-34"), ("Bill S-2", "S-2"), ("bill no. 12", "C-12"),
])
def test_bill_numbers_normalized_without_model(jev, monkeypatch, text, expected):
    stub = jev(_StubJev(_result("concept", 0.99)))
    _forbid_llm(monkeypatch)
    result = service.classify_query(text)
    assert (result["type"], result["normalized"]) == ("bill", expected)
    assert stub.calls == 0
