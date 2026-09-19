"""Provider routing tests: fully mocked, with no network or dotenv access."""

from unittest.mock import MagicMock, patch


def _response(data):
    response = MagicMock()
    response.json.return_value = data
    response.raise_for_status.return_value = None
    return response


def test_chatbox_text_skips_gemini_and_deepseek_records_openrouter(monkeypatch):
    monkeypatch.setenv("CHATBOX_OPENROUTER_ONLY", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("LLM_COMPLETIONS_URL", "https://openrouter.example/v1/chat/completions")
    from llm_api import gemini_api, deepseek_api

    monkeypatch.setattr(deepseek_api, "COMPLETIONS_URL", "https://openrouter.example/v1/chat/completions")

    assert gemini_api.call_gemini_text("private prompt") is None
    payload = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3}}
    with patch("llm_api.deepseek_api.deepseek_session.request", return_value=_response(payload)) as request, \
         patch("core.spend_tracker.spend_tracker.is_over_cap", return_value=False), \
         patch("core.spend_tracker.spend_tracker.record_cost") as record:
        assert deepseek_api.ask_deepseek("private prompt") == "ok"
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
    record.assert_called_once_with("openrouter", deepseek_api.DEEPSEEK_MODEL, 2, 3)


def test_chatbox_vision_uses_openrouter_multimodal_and_tracks(monkeypatch, tmp_path):
    monkeypatch.setenv("CHATBOX_OPENROUTER_ONLY", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_VISION_MODEL", "vision/model")
    image = tmp_path / "page.png"
    image.write_bytes(b"png-data")
    data = {"choices": [{"message": {"content": '{"page_title":"T","raw_text":"R"}'}}], "usage": {"prompt_tokens": 4, "completion_tokens": 5}}
    from llm_api import gemini_api

    with patch("llm_api.openrouter_api.generic_session.request", return_value=_response(data)) as request, \
         patch("core.spend_tracker.spend_tracker.is_over_cap", return_value=False), \
         patch("core.spend_tracker.spend_tracker.record_cost") as record:
        result = gemini_api._call_gemini([str(image)])
    assert result["page_title"] == "T"
    sent = request.call_args.kwargs["json"]
    assert sent["model"] == "vision/model"
    assert sent["messages"][0]["content"][1]["type"] == "image_url"
    record.assert_called_once_with("openrouter", "vision/model", 4, 5)


def test_chatbox_budget_blocks_before_any_provider_request(monkeypatch):
    monkeypatch.setenv("CHATBOX_OPENROUTER_ONLY", "1")
    from llm_api import deepseek_api, gemini_api
    with patch("core.spend_tracker.spend_tracker.is_over_cap", return_value=True), \
         patch("llm_api.deepseek_api.deepseek_session.request") as deepseek_request, \
         patch("llm_api.openrouter_api.generic_session.request") as openrouter_request:
        try:
            deepseek_api.ask_deepseek("x")
        except RuntimeError:
            pass
        assert gemini_api._call_gemini(["not-read.png"]) is None
    deepseek_request.assert_not_called()
    openrouter_request.assert_not_called()
