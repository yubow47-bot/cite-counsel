"""One OpenAI-compatible chat call with native tool calling."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def _post(model: str, messages: list[dict], tools: list[dict], timeout: float, *, disable_reasoning: bool):
    from llm_api.openrouter_api import post_chat_completion
    body = {"model": model, "messages": messages, "temperature": 0}
    if disable_reasoning:
        # Default-thinking models spend seconds reasoning before a tool call;
        # some models (e.g. GLM flash tiers) reject this and require reasoning on.
        body["reasoning"] = {"enabled": False}
    if tools:
        body["tools"] = tools
    return post_chat_completion(body, read_timeout=timeout)


def chat(model: str, messages: list[dict], tools: list[dict], *, timeout: float = 60) -> dict:
    """Return the assistant message (may carry ``tool_calls``)."""
    from core.spend_tracker import spend_tracker
    from llm_api.openrouter_api import track_usage
    if spend_tracker.is_over_cap():
        raise LLMError("今日模型费用已达上限。")
    try:
        response = _post(model, messages, tools, timeout, disable_reasoning=True)
        if response.status_code == 400 and "reasoning is mandatory" in response.text.lower():
            response = _post(model, messages, tools, timeout, disable_reasoning=False)
        response.raise_for_status()
        data = response.json()
    except ValueError as exc:
        raise LLMError("没有配置模型 API Key。") from exc
    except Exception as exc:
        logger.error("llm.chat failed for model %s: %s", model, exc)
        raise LLMError("模型服务暂时不可用。") from exc
    track_usage(model, data)
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("模型返回格式不正确。") from exc


def configured() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())
