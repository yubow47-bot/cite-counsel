"""One OpenAI-compatible chat call with native tool calling."""

from __future__ import annotations

import json
import logging
import os
import re

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
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("模型返回格式不正确。") from exc
    if tools:
        return _recover_leaked_calls(message)
    # No tools were offered, so nothing to run -- just keep the markup off screen.
    if isinstance(message.get("content"), str) and "<tool_call>" in message["content"]:
        return {**message, "content": _LEAKED_CALL.sub("", message["content"]).strip()}
    return message


# GLM sometimes writes a tool call in its own text format instead of the
# structured field, and the provider passes it through as prose:
#   <tool_call>load_plugin<arg_key>name</arg_key><arg_value>web</arg_value></tool_call>
# Shown as-is, the user sees markup and the call never runs.
_LEAKED_CALL = re.compile(r"<tool_call>\s*([\w.-]+)\s*(.*?)</tool_call>", re.S)
_LEAKED_ARG = re.compile(r"<arg_key>\s*(.*?)\s*</arg_key>\s*<arg_value>(.*?)</arg_value>", re.S)


def _leaked_value(text: str):
    # Only structured values are JSON; "2016" must stay the string it was.
    text = text.strip()
    if text[:1] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            pass
    return text


def _recover_leaked_calls(message: dict) -> dict:
    content = message.get("content")
    if message.get("tool_calls") or not isinstance(content, str) or "<tool_call>" not in content:
        return message
    calls = []
    for n, match in enumerate(_LEAKED_CALL.finditer(content)):
        arguments = {key: _leaked_value(value) for key, value in _LEAKED_ARG.findall(match.group(2))}
        calls.append({"id": f"leaked_{n}", "type": "function",
                      "function": {"name": match.group(1), "arguments": json.dumps(arguments, ensure_ascii=False)}})
    if not calls:
        return message
    logger.info("Recovered %d tool call(s) the model wrote as text", len(calls))
    return {**message, "content": _LEAKED_CALL.sub("", content).strip(), "tool_calls": calls}


def configured() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())
