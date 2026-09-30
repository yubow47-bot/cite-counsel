"""One OpenAI-compatible chat call with native tool calling."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelProfile:
    think_tags: bool = True
    text_tool_calls: bool = True


_PROFILES = {
    "openai/": ModelProfile(think_tags=False, text_tool_calls=False),
    "anthropic/": ModelProfile(think_tags=False, text_tool_calls=False),
}


def profile_for(model: str) -> ModelProfile:
    name = model.casefold()
    return next((profile for prefix, profile in _PROFILES.items() if name.startswith(prefix)), ModelProfile())


# Some models write their chain of thought as <think> tags inside ``content``
# instead of the structured ``reasoning`` field. Either way it becomes a
# collapsible thinking block and stays out of the reply text.
_THINK_PAIR = re.compile(r"<think>(.*?)</think>", re.S)
_THINK_OPEN = re.compile(r"<think>(.*)\Z", re.S)


def split_thinking(content: str, model: str = "") -> tuple[str, str]:
    """(reply, thinking): every <think> section moves out of the content. An
    unclosed <think> (a turn cut off mid-thought) counts as thinking to the
    end of the text; sections join with blank lines."""
    if not profile_for(model).think_tags or ('<think>' not in content and '</think>' not in content):
        return content, ""
    without_pairs = _THINK_PAIR.sub("", content)
    parts = _THINK_PAIR.findall(content)
    if '</think>' in without_pairs and '<think>' not in content:
        # A stray closer with no opener: drop it, nothing is thinking.
        return without_pairs.replace('</think>', "").strip(), ""
    open_without_pair = _THINK_OPEN.search(without_pairs)
    if open_without_pair:
        parts.append(open_without_pair.group(1))
        without_pairs = without_pairs[:open_without_pair.start()]
    reply = without_pairs.replace('</think>', "").strip()
    return reply, "\n\n".join(part.strip() for part in parts if part.strip())


class ThinkSniffer:
    """The streaming counterpart to ``split_thinking``: watches content
    deltas as they arrive for a leading ``<think>...</think>`` block (a model
    that has no structured ``reasoning`` field puts its whole chain of
    thought there instead) and peels reasoning text off it live, chunk by
    chunk, instead of only once the full reply is in. Content that is never
    a think tag is recognised within the first few characters and passed
    straight through after that -- no per-delta overhead once resolved."""

    _OPEN, _CLOSE = "<think>", "</think>"

    def __init__(self):
        self._buffer = ""
        self._mode = "sniff"   # "sniff" -> "think" -> "content"
        self.plain: list[str] = []

    def feed(self, chunk: str) -> list[str]:
        """Reasoning text pulled out of this chunk, if any, to show live."""
        if self._mode == "content":
            self.plain.append(chunk)
            return []
        self._buffer += chunk
        if self._mode == "sniff":
            if self._buffer.startswith(self._OPEN):
                self._buffer = self._buffer[len(self._OPEN):]
                self._mode = "think"
            elif len(self._buffer) < len(self._OPEN) and self._OPEN.startswith(self._buffer):
                return []          # still ambiguous -- wait for more
            else:
                self._mode = "content"
                self.plain.append(self._buffer)
                self._buffer = ""
                return []
        if self._mode == "think":
            idx = self._buffer.find(self._CLOSE)
            if idx == -1:
                safe = len(self._buffer) - (len(self._CLOSE) - 1)
                if safe <= 0:
                    return []
                out, self._buffer = self._buffer[:safe], self._buffer[safe:]
                return [out]
            out, rest = self._buffer[:idx], self._buffer[idx + len(self._CLOSE):]
            self._buffer, self._mode = "", "content"
            if rest:
                self.plain.append(rest)
            return [out] if out else []
        return []

    def flush(self) -> list[str]:
        """An unclosed <think> section remains reasoning through stream end."""
        if self._mode == "think" and self._buffer:
            text, self._buffer = self._buffer, ""
            return [text]
        if self._buffer:
            self.plain.append(self._buffer)
            self._buffer = ""
        return []


class PlainSniffer:
    def __init__(self):
        self.plain: list[str] = []

    def feed(self, chunk: str) -> list[str]:
        self.plain.append(chunk)
        return []

    def flush(self) -> list[str]:
        return []


def think_sniffer(model: str) -> ThinkSniffer | PlainSniffer:
    return ThinkSniffer() if profile_for(model).think_tags else PlainSniffer()


def plain_schema(schema: dict) -> dict:
    """A tool's JSON schema with every ``$ref`` inlined, schema titles
    dropped, and ``anyOf [X, null]`` (an optional field) collapsed to X --
    nested parameters (record__compose's per-field objects) reach the model
    spelled out instead of behind a reference some providers do not follow."""
    defs = schema.get("$defs", {})

    def walk(node, properties=False):
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if properties:                       # keys here are field names, not keywords
            return {name: walk(value) for name, value in node.items()}
        if "$ref" in node:
            return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
        out = {key: walk(value, key == "properties") for key, value in node.items()
               if key not in ("$defs", "title")}
        options = out.get("anyOf")
        if isinstance(options, list):
            kept = [option for option in options if option.get("type") != "null"]
            if len(kept) == 1:
                out = {**kept[0], **{k: v for k, v in out.items() if k != "anyOf"}}
        return out

    return walk(schema)



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
        raise LLMError("Today's model spend cap has been reached.")
    try:
        response = _post(model, messages, tools, timeout, include_reasoning=True)
        text = response.text.lower()
        if response.status_code == 400 and "reasoning" in text and "mandatory" not in text:
            response = _post(model, messages, tools, timeout, include_reasoning=False)
        response.raise_for_status()
        data = response.json()
    except ValueError as exc:
        raise LLMError("No model API key is configured.") from exc
    except Exception as exc:
        logger.error("llm.chat failed for model %s: %s", model, exc)
        raise LLMError("The model service is temporarily unavailable.") from exc
    track_usage(model, data)
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("The model returned an unexpected format.") from exc
    return _finalize(message, tools, profile_for(model))


def _finalize(message: dict, tools: list[dict], profile: ModelProfile | None = None) -> dict:
    profile = profile or ModelProfile()
    if tools and profile.text_tool_calls:
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
        raise LLMError("Today's model spend cap has been reached.")
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
            except ValueError as exc:
                raise LLMError("The model returned malformed stream data.") from exc
            if not isinstance(chunk, dict):
                raise LLMError("The model returned malformed stream data.")
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
        raise LLMError("No model API key is configured.") from exc
    except LLMError:
        raise
    except Exception as exc:
        logger.error("llm.chat_stream failed for model %s: %s", model, exc)
        raise LLMError("The model service is temporarily unavailable.") from exc
    if usage:
        track_usage(model, {"usage": usage})
    message = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning"] = "".join(reasoning_parts)
    ordered = [tool_calls[i] for i in sorted(tool_calls)]
    if ordered:
        message["tool_calls"] = [{"id": c["id"] or f"call_{n}", "type": "function", "function": c["function"]}
                                 for n, c in enumerate(ordered)]
    yield "done", _finalize(message, tools, profile_for(model))


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


def strip_leaked_markup(content: str) -> str:
    return _LEAKED_CALL.sub("", content).strip()


def configured() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())
