"""Vision extraction goes through OpenRouter, is tracked, and respects the spend cap."""

from unittest.mock import MagicMock, patch


def _response(data):
    response = MagicMock()
    response.json.return_value = data
    response.raise_for_status.return_value = None
    return response


def test_vision_uses_openrouter_multimodal_and_tracks(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_VISION_MODEL", "vision/model")
    image = tmp_path / "page.png"
    image.write_bytes(b"png-data")
    data = {"choices": [{"message": {"content": '{"page_title":"T","raw_text":"R"}'}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 5}}
    from llm_api import vision

    with patch("llm_api.openrouter_api.generic_session.request", return_value=_response(data)) as request, \
         patch("core.spend_tracker.spend_tracker.is_over_cap", return_value=False), \
         patch("core.spend_tracker.spend_tracker.record_cost") as record:
        result = vision._call_vision([str(image)])
    assert result["page_title"] == "T"
    sent = request.call_args.kwargs["json"]
    assert sent["model"] == "vision/model"
    assert sent["messages"][0]["content"][1]["type"] == "image_url"
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
    record.assert_called_once_with("openrouter", "vision/model", 4, 5)


def test_budget_blocks_before_any_request():
    from llm_api import vision
    with patch("core.spend_tracker.spend_tracker.is_over_cap", return_value=True), \
         patch("llm_api.openrouter_api.generic_session.request") as request:
        assert vision._call_vision(["not-read.png"]) is None
    request.assert_not_called()
