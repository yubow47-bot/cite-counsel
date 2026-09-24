"""One OpenAI-compatible chat call with native tool calling."""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def _post(model: str, messages: list[dict], tools: list[dict], timeout: float, *,
          include_reasoning: bool, stream: bool = False):
    from llm_api.openrouter_api import post_chat_completion
    body = {"model": model, "messages": messages, "temperature": 0}
    # Reasoning is requested on every call so the user can see how the model
    # decided to use a plugin, not just its final reply. A model that doesn't
    # support the field ignores it; one that rejects it is retried below
    # with the field left out.
    if include_reasoning:
        body["reasoning"] = {"enabled": True}
    if tools:
        body["tools"] = tools
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return post_chat_completion(body, read_timeout=timeout, stream=stream)


def chat(model: str, messages: list[dict], tools: list[dict], *, timeout: float = 60) -> dict:
    """Return the assistant message (may carry ``tool_calls`` and ``reasoning``)."""
    from core.spend_tracker import spend_tracker
    from llm_api.openrouter_api import track_usage
    if spend_tracker.is_over_cap():
        raise LLMError("今日模型费用已达上限。")
    try:
        response = _post(model, messages, tools, timeout, include_reasoning=True)
        text = response.text.lower()
        if response.status_code == 400 and "reasoning" in text and "mandatory" not in text:
            response = _post(model, messages, tools, timeout, include_reasoning=False)
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
    return _finalize(message, tools)


def _finalize(message: dict, tools: list[dict]) -> dict:
    if tools:
        return _recover_leaked_calls(message)
    # No tools were offered, so nothing to run -- just keep the markup off screen.
    if isinstance(message.get("content"), str) and "<tool_call>" in message["content"]:
        return {**message, "content": _LEAKED_CALL.sub("", message["content"]).strip()}
    return message


def chat_stream(model: str, messages: list[dict], tools: list[dict], *, timeout: float = 60):
    """Same call as ``chat``, but a generator: ``("reasoning" | "content", delta)``
    events as tokens arrive, then one final ``("done", message)`` with the
    same assembled message ``chat()`` would have returned (usable for the
    tool-calling pipeline exactly as before). Lets the page show the model's
    thinking as it happens instead of only once the whole turn is done."""
    from core.spend_tracker import spend_tracker
    from llm_api.openrouter_api import track_usage
    if spend_tracker.is_over_cap():
        raise LLMError("今日模型费用已达上限。")
    try:
        response = _post(model, messages, tools, timeout, include_reasoning=True, stream=True)
        if response.status_code == 400:
            text = response.text.lower()
            if "reasoning" in text and "mandatory" not in text:
                response.close()
                response = _post(model, messages, tools, timeout, include_reasoning=False, stream=True)
        response.raise_for_status()
        # requests guesses the encoding from Content-Type; OpenRouter's SSE
        # response carries no charset, so it falls back to Latin-1 and every
        # multi-byte character (em dashes, curly quotes, Chinese text) comes
        # out as mojibake. The body is UTF-8 -- say so explicitly.
        response.encoding = "utf-8"
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        usage = None
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            reasoning = delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                reasoning_parts.append(reasoning)
                yield "reasoning", reasoning
            content = delta.get("content")
            if isinstance(content, str) and content:
                content_parts.append(content)
                yield "content", content
            for call in delta.get("tool_calls") or []:
                index = call.get("index", 0)
                slot = tool_calls.setdefault(index, {"id": "", "type": "function",
                                                      "function": {"name": "", "arguments": ""}})
                if call.get("id"):
                    slot["id"] = call["id"]
                function = call.get("function") or {}
                if function.get("name"):
                    slot["function"]["name"] += function["name"]
                if function.get("arguments"):
                    slot["function"]["arguments"] += function["arguments"]
    except ValueError as exc:
        raise LLMError("没有配置模型 API Key。") from exc
    except LLMError:
        raise
    except Exception as exc:
        logger.error("llm.chat_stream failed for model %s: %s", model, exc)
        raise LLMError("模型服务暂时不可用。") from exc
    if usage:
        track_usage(model, {"usage": usage})
    message = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning"] = "".join(reasoning_parts)
    ordered = [tool_calls[i] for i in sorted(tool_calls)]
    if ordered:
        message["tool_calls"] = [{"id": c["id"] or f"call_{n}", "type": "function", "function": c["function"]}
                                 for n, c in enumerate(ordered)]
    yield "done", _finalize(message, tools)


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
