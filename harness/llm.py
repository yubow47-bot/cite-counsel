"""One OpenAI-compatible chat call with native tool calling."""

from __future__ import annotations

import copy
import json
import logging
import os
import queue
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMDeadline(LLMError):
    """The caller's wall-clock budget expired; no final message is accepted."""


class LLMCancelled(LLMError):
    """The caller cancelled this model request."""


def _check_control(deadline: float | None, cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise LLMCancelled("The model request was cancelled.")
    if deadline is not None and time.monotonic() >= deadline:
        raise LLMDeadline("The model request reached its time limit.")


def _close_response(response) -> None:
    if response is not None:
        try:
            getattr(response, "close", lambda: None)()
        except Exception:
            logger.debug("Could not close the model response.", exc_info=True)


def _response_events(model, messages, tools, timeout, *, stream, deadline=None, stopped=None,
                     **options):
    """Only HTTP I/O runs here; interpretation and accounting stay with the caller."""
    response = None
    data = None
    try:
        for include_reasoning in (True, False):
            if stopped is not None and stopped.is_set():
                return
            _check_control(deadline, None)
            remaining = timeout if deadline is None else min(timeout, deadline - time.monotonic())
            response = _post(model, messages, tools, max(0.001, remaining),
                             include_reasoning=include_reasoning, stream=stream, **options)
            if stopped is not None and stopped.is_set():
                return
            if include_reasoning and response.status_code == 400:
                text = response.text.lower()
                if "reasoning" in text and "mandatory" not in text:
                    _close_response(response)
                    response = None
                    continue
            response.raise_for_status()
            if stream:
                # OpenRouter SSE has no charset; requests otherwise guesses Latin-1.
                response.encoding = "utf-8"
                for line in response.iter_lines(decode_unicode=True):
                    if stopped is not None and stopped.is_set():
                        return
                    yield line
            else:
                data = response.json()
            break
    finally:
        _close_response(response)
    if not stream:
        yield data


def _model_io(model, messages, tools, timeout, *, stream, deadline=None, cancelled=None,
              **options) -> Iterator:
    """Bound caller latency even while HTTP blocks or dribbles tokens forever.

    The optional guard owns a small queue and an I/O-only daemon. Stopping the
    consumer discards late data; it never lets that worker touch session state,
    interpret tool calls, or record usage. The worker closes its response in
    ``finally`` once the underlying read/connect returns. Python cannot kill a
    blocked HTTP/DNS operation, and socket timeouts are idle/per-operation limits,
    so transport cleanup may outlive the caller's deadline.
    """
    if deadline is None and cancelled is None:
        yield from _response_events(model, messages, tools, timeout, stream=stream, **options)
        return
    _check_control(deadline, cancelled)
    # A timed-out request must not serialize a subsequently edited conversation.
    messages, tools = copy.deepcopy((messages, tools))
    events = queue.Queue(maxsize=8)
    stopped = threading.Event()

    def send(kind, value=None):
        while not stopped.is_set():
            try:
                events.put((kind, value), timeout=0.05)
                return True
            except queue.Full:
                pass
        return False

    def produce():
        source = _response_events(model, messages, tools, timeout, stream=stream,
                                  deadline=deadline, stopped=stopped, **options)
        try:
            for value in source:
                if not send("value", value):
                    return
        except BaseException as exc:
            send("error", exc)
        else:
            send("end")
        finally:
            source.close()

    threading.Thread(target=produce, name="model-http", daemon=True).start()
    try:
        while True:
            _check_control(deadline, cancelled)
            wait = 0.05 if deadline is None else min(0.05, max(0.001, deadline - time.monotonic()))
            try:
                kind, value = events.get(timeout=wait)
            except queue.Empty:
                continue
            # The result may have arrived just as the deadline/cancellation fired.
            _check_control(deadline, cancelled)
            if kind == "error":
                raise value
            if kind == "end":
                return
            yield value
    finally:
        stopped.set()


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
          include_reasoning: bool, stream: bool = False, reasoning_effort: str | None = None,
          max_tokens: int | None = None, tool_choice=None):
    from llm_api.openrouter_api import post_chat_completion
    body = {"model": model, "messages": messages, "temperature": 0}
    # Reasoning is requested on every call so the user can see how the model
    # decided to use a plugin, not just its final reply. A model that doesn't
    # support the field ignores it; one that rejects it is retried below
    # with the field left out.
    if include_reasoning:
        effort = reasoning_effort or ("low" if tools else None)
        body["reasoning"] = {"enabled": True, **({"effort": effort} if effort else {})}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if tools:
        body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return post_chat_completion(body, connect_timeout=min(2.7, timeout), read_timeout=timeout, stream=stream)


def chat(model: str, messages: list[dict], tools: list[dict], *, timeout: float = 60,
         reasoning_effort: str | None = None, max_tokens: int | None = None, tool_choice=None,
         deadline: float | None = None, cancelled: Callable[[], bool] | None = None) -> dict:
    """Return a complete assistant message; ``deadline`` uses ``time.monotonic()``."""
    from core.spend_tracker import spend_tracker
    from llm_api.openrouter_api import track_usage
    if spend_tracker.is_over_cap():
        raise LLMError("Today's model spend cap has been reached.")
    io = _model_io(model, messages, tools, timeout, stream=False, deadline=deadline, cancelled=cancelled,
                   reasoning_effort=reasoning_effort, max_tokens=max_tokens, tool_choice=tool_choice)
    try:
        data = next(io)
    except ValueError as exc:
        raise LLMError("No model API key is configured.") from exc
    except LLMError:
        raise
    except Exception as exc:
        logger.error("llm.chat failed for model %s: %s", model, exc)
        raise LLMError("The model service is temporarily unavailable.") from exc
    finally:
        io.close()
    _check_control(deadline, cancelled)
    track_usage(model, data)
    try:
        if data["choices"][0].get("finish_reason") not in {None, "stop", "tool_calls", "function_call"}:
            raise LLMError("The model response did not complete; its partial response was not committed.")
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("The model returned an unexpected format.") from exc
    message = _finalize(message, tools, profile_for(model))
    _check_control(deadline, cancelled)
    return message


def _finalize(message: dict, tools: list[dict], profile: ModelProfile | None = None) -> dict:
    profile = profile or ModelProfile()
    if tools and profile.text_tool_calls:
        return _recover_leaked_calls(message)
    # No tools were offered, so nothing to run -- just keep the markup off screen.
    if isinstance(message.get("content"), str) and "<tool_call>" in message["content"]:
        return {**message, "content": _LEAKED_CALL.sub("", message["content"]).strip()}
    return message


def chat_stream(model: str, messages: list[dict], tools: list[dict], *, timeout: float = 60,
                deadline: float | None = None, cancelled: Callable[[], bool] | None = None):
    """Same call as ``chat``, but a generator: ``("reasoning" | "content", delta)``
    events as tokens arrive, then one final ``("done", message)`` with the
    same assembled message ``chat()`` would have returned (usable for the
    tool-calling pipeline exactly as before). Lets the page show the model's
    thinking as it happens instead of only once the whole turn is done.
    ``deadline`` is absolute monotonic time; cancellation/deadlines never emit
    ``done`` for a partial message, even if the HTTP transport is stalled."""
    from core.spend_tracker import spend_tracker
    from llm_api.openrouter_api import track_usage
    if spend_tracker.is_over_cap():
        raise LLMError("Today's model spend cap has been reached.")
    io = _model_io(model, messages, tools, timeout, stream=True, deadline=deadline, cancelled=cancelled)
    usage = None
    try:
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        usage = None
        completed = False
        finish_reason = None
        saw_choice = False
        for line in io:
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                completed = True
                break
            try:
                chunk = json.loads(payload)
            except ValueError as exc:
                raise LLMError("The model returned malformed stream data.") from exc
            if not isinstance(chunk, dict):
                raise LLMError("The model returned malformed stream data.")
            if chunk.get("error"):
                raise LLMError("The model stream reported an error; this partial response was not committed.")
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                continue
            if not isinstance(choices, list) or not isinstance(choices[0], dict):
                raise LLMError("The model returned malformed stream choices.")
            saw_choice = True
            reason = choices[0].get("finish_reason")
            if reason is not None:
                finish_reason = reason
                if reason not in {"stop", "tool_calls", "function_call"}:
                    raise LLMError(f"The model response did not complete ({str(reason)[:60]}). "
                                   "Its partial tool calls were not executed.")
                completed = True
            delta = choices[0].get("delta") or {}
            if not isinstance(delta, dict):
                raise LLMError("The model returned malformed stream data.")
            reasoning = delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                reasoning_parts.append(reasoning)
                yield "reasoning", reasoning
            content = delta.get("content")
            if isinstance(content, str) and content:
                content_parts.append(content)
                yield "content", content
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict) or type(call.get("index", 0)) is not int:
                    raise LLMError("The model returned malformed tool call data.")
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
        if not completed or not saw_choice:
            raise LLMError("The model stream ended before completion; its partial response was not committed.")
    except ValueError as exc:
        raise LLMError("No model API key is configured.") from exc
    except LLMError:
        raise
    except Exception as exc:
        logger.error("llm.chat_stream failed for model %s: %s", model, exc)
        raise LLMError("The model service is temporarily unavailable.") from exc
    finally:
        io.close()
        if usage:
            track_usage(model, {"usage": usage})
    message = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning"] = "".join(reasoning_parts)
    ordered = [tool_calls[i] for i in sorted(tool_calls)]
    if ordered:
        message["tool_calls"] = [{"id": c["id"] or f"call_{n}", "type": "function", "function": c["function"]}
                                 for n, c in enumerate(ordered)]
    message["finish_reason"] = finish_reason or "stream_done"
    message = _finalize(message, tools, profile_for(model))
    _check_control(deadline, cancelled)
    yield "done", message


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
    # Recover only a complete protocol message. A model may quote a source's
    # tool markup while explaining why it is untrusted; extracting calls out
    # of that ordinary prose would turn the explanation into an execution.
    # Code fences, labels and other surrounding text therefore remain data.
    if _LEAKED_CALL.sub("", content).strip():
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
