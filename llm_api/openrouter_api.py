"""Small OpenRouter adapter shared by every model call.

This module deliberately does not load dotenv files.  Chatbox supplies its
configuration through the process environment.
"""

import os

from local_tools.utils import generic_session, request_with_retry


OPENROUTER_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


def check_budget() -> bool:
    """Return False before a paid request once the daily cap is hit."""
    try:
        from core.spend_tracker import spend_tracker
        return not spend_tracker.is_over_cap()
    except Exception:
        return False


def completions_url() -> str:
    """Fixed on purpose: the OpenRouter key must never be sent to an endpoint taken from the environment."""
    return OPENROUTER_COMPLETIONS_URL


def api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY") or ""
    if not key:
        raise ValueError("OPENROUTER_API_KEY (or LLM_API_KEY) not configured.")
    return key


def post_chat_completion(body: dict, *, connect_timeout: float = 2.7, read_timeout: float = 30, stream: bool = False):
    """Issue one OpenAI-compatible completion request; POST is never retried.

    ``stream=True`` returns the response with the body unread (SSE lines,
    ``response.iter_lines()``) instead of buffering the whole reply first --
    what lets the caller show reasoning as it arrives instead of after the
    model finishes thinking.
    """
    if not check_budget():
        raise RuntimeError("Daily LLM spend cap reached or unavailable.")
    return request_with_retry(
        generic_session, "POST", completions_url(), retries=0,
        connect_timeout=connect_timeout, read_timeout=read_timeout,
        headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"},
        json=body, stream=stream,
    )


def choice_text(data: dict) -> str:
    """Extract the standard OpenAI-compatible first assistant content."""
    return data["choices"][0]["message"]["content"]


def track_usage(model: str, data: dict | None) -> None:
    """Best-effort OpenRouter token accounting."""
    try:
        usage = (data or {}).get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        if input_tokens or output_tokens:
            from core.spend_tracker import spend_tracker
            spend_tracker.record_cost("openrouter", model, input_tokens, output_tokens)
    except Exception:
        pass
