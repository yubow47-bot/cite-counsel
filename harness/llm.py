"""One OpenAI-compatible chat call with native tool calling."""

from __future__ import annotations

import os


class LLMError(RuntimeError):
    pass


def chat(model: str, messages: list[dict], tools: list[dict], *, timeout: float = 60) -> dict:
    """Return the assistant message (may carry ``tool_calls``)."""
    from core.spend_tracker import spend_tracker
    from llm_api.openrouter_api import post_chat_completion, track_usage
    if spend_tracker.is_over_cap():
        raise LLMError("今日模型费用已达上限。")
    body = {"model": model, "messages": messages, "temperature": 0,
            # Default-thinking models spend seconds reasoning before a tool call.
            "reasoning": {"enabled": False}}
    if tools:
        body["tools"] = tools
    try:
        response = post_chat_completion(body, read_timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except ValueError as exc:
        raise LLMError("没有配置模型 API Key。") from exc
    except Exception as exc:
        raise LLMError("模型服务暂时不可用。") from exc
    track_usage(model, data)
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("模型返回格式不正确。") from exc


def configured() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())
