"""Offline contract tests for the fail-closed JEV decision layer."""

from unittest.mock import MagicMock

import pytest
import requests

from core.decisions.client import DecisionError
from core.decisions.fake_client import FakeDecisionClient
from core.decisions.jev_client import JevClient, JevConfig
from core.decisions.models import ChoiceQuestion, DecisionResult
from core.decisions.policies import DecisionPolicy, DecisionPolicyRegistry
from core.spend_tracker import SpendTracker, normalize_model_key


def _question() -> ChoiceQuestion:
    return ChoiceQuestion(
        question_id="document_type.v1",
        instructions="Classify the document.",
        criteria={"book": "A book", "case": "A court decision", "other": "Neither"},
    )


def _payload(**overrides):
    response = {
        "model": "typesafe/jev-1.13",
        "answers": {
            "document_type.v1": {
                "type": "choice",
                "choice": "case",
                "probabilities": {"book": 0.1, "case": 0.8, "other": 0.1},
                "confidence": 0.75,
            }
        },
        "usage": {"input_tokens": 100, "output_tokens": 0},
    }
    response.update(overrides)
    return response


def _client(response_payload):
    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = response_payload
    response.raise_for_status.return_value = None
    session.post.return_value = response
    return JevClient(JevConfig("openrouter", "https://example.invalid", "~typesafe/jev-latest", "test-key"), session), session


@pytest.mark.parametrize("alias", ["jev-latest", "typesafe/jev-1.13", "~typesafe/jev-latest"])
def test_jev_aliases_use_the_known_zero_output_price(alias):
    assert normalize_model_key(alias) == "jev"
    tracker = SpendTracker.__new__(SpendTracker)
    assert tracker._compute_cost("openrouter", alias, 1_000_000, 1_000_000) == pytest.approx(0.042)


def test_unknown_model_keeps_conservative_fallback():
    assert normalize_model_key("typesafe/jev-future") == "typesafe/jev-future"


def test_fake_client_rejects_choices_outside_server_options():
    fake = FakeDecisionClient({
        "document_type.v1": DecisionResult("document_type.v1", "invented", {}, 1.0, "fake", 0),
    })
    with pytest.raises(DecisionError):
        fake.decide_choice("document", _question())


def test_jev_client_validates_and_records_closed_set_answer(monkeypatch):
    client, session = _client(_payload())
    recorded = []
    monkeypatch.setattr("core.decisions.jev_client.spend_tracker.record_cost", lambda *args: recorded.append(args))
    result = client.decide_choice("decision text", _question())
    assert result.selected_id == "case"
    assert result.probabilities["case"] == 0.8
    assert recorded == [("openrouter", "typesafe/jev-1.13", 100, 0)]
    assert session.post.call_args.kwargs["json"]["questions"]["document_type.v1"]["criteria"] == _question().criteria


def test_jev_client_rejects_rewritten_candidate_id():
    payload = _payload()
    payload["answers"]["document_type.v1"]["choice"] = "new_case_name"
    client, _ = _client(payload)
    with pytest.raises(DecisionError):
        client.decide_choice("decision text", _question())


def test_jev_client_rejects_missing_or_extra_probability_options():
    payload = _payload()
    payload["answers"]["document_type.v1"]["probabilities"] = {"case": 1.0}
    client, _ = _client(payload)
    with pytest.raises(DecisionError):
        client.decide_choice("decision text", _question())


def test_jev_client_fails_closed_without_credentials():
    client = JevClient(JevConfig("openrouter", "https://example.invalid", "~typesafe/jev-latest", ""))
    with pytest.raises(DecisionError):
        client.decide_choice("decision text", _question())


def test_official_typesafe_environment_configuration(monkeypatch):
    monkeypatch.setenv("JEV_PROVIDER", "typesafe")
    monkeypatch.setenv("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
    monkeypatch.setenv("JEV_MODEL", "jev-latest")
    monkeypatch.setenv("TYPESAFE_API_KEY", "official-test-key")
    config = JevConfig.from_environment()
    assert config.provider == "typesafe"
    assert config.endpoint == "https://api.typesafe.ai/v1/systemone"
    assert config.model == "jev-latest"
    assert config.api_key == "official-test-key"


def test_jev_client_converts_timeout_to_explicit_failure():
    session = MagicMock()
    session.post.side_effect = requests.Timeout("timed out")
    client = JevClient(JevConfig("openrouter", "https://example.invalid", "~typesafe/jev-latest", "test-key"), session)
    with pytest.raises(DecisionError, match="JEV request failed"):
        client.decide_choice("decision text", _question())


def test_usage_is_recorded_even_when_schema_validation_rejects_the_answer(monkeypatch):
    payload = _payload()
    payload["answers"]["document_type.v1"]["choice"] = "provider-invented-id"
    client, _ = _client(payload)
    recorded = []
    monkeypatch.setattr("core.decisions.jev_client.spend_tracker.record_cost", lambda *args: recorded.append(args))
    with pytest.raises(DecisionError):
        client.decide_choice("decision text", _question())
    assert recorded == [("openrouter", "typesafe/jev-1.13", 100, 0)]


def test_unregistered_and_low_confidence_policies_abstain():
    result = DecisionResult("document_type.v1", "case", {"case": 1.0}, 0.7, "fake", 0)
    assert not DecisionPolicyRegistry().permits(result, "en")
    registry = DecisionPolicyRegistry({"document_type.v1": DecisionPolicy(0.8, frozenset({"en", "fr"}))})
    assert not registry.permits(result, "en")
    assert not registry.permits(result, "zh")
