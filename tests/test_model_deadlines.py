"""Model wall-clock limits work without another token arriving from the network."""

import json
import threading
import time

import pytest

from harness import llm


def chunk(delta=None, finish=None):
    return "data: " + json.dumps({"choices": [{"delta": delta or {}, "finish_reason": finish}]})


class Response:
    def __init__(self, *, status=200, lines=None, data=None, text=""):
        self.status_code = status
        self.text = text
        self.lines = lines or []
        self.data = data or {"choices": [{"message": {"role": "assistant", "content": "Complete"},
                                         "finish_reason": "stop"}]}
        self.closed = threading.Event()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP failure")

    def json(self):
        return self.data

    def iter_lines(self, **kwargs):
        yield from self.lines

    def close(self):
        self.closed.set()


@pytest.fixture(autouse=True)
def isolated_usage(monkeypatch):
    from core.spend_tracker import spend_tracker
    from llm_api import openrouter_api

    monkeypatch.setattr(spend_tracker, "is_over_cap", lambda: False)
    calls = []
    monkeypatch.setattr(openrouter_api, "track_usage", lambda *args: calls.append(args))
    return calls


@pytest.mark.parametrize("stream", [False, True])
def test_deadline_returns_while_connect_is_blocked_and_discards_late_reply(monkeypatch, isolated_usage, stream):
    release = threading.Event()
    entered = threading.Event()
    response = Response(lines=[chunk({"tool_calls": [{"index": 0, "function": {
        "name": "should_never_execute", "arguments": "{}"}}]}, "tool_calls"), "data: [DONE]"])
    interpreted = []

    def post(*args, **kwargs):
        entered.set()
        release.wait(2)
        return response

    monkeypatch.setattr(llm, "_post", post)
    monkeypatch.setattr(llm, "_finalize", lambda *args: interpreted.append(args))
    started = time.monotonic()
    try:
        with pytest.raises(llm.LLMDeadline):
            if stream:
                list(llm.chat_stream("fixture", [], [], deadline=started + 0.08))
            else:
                llm.chat("fixture", [], [], deadline=started + 0.08)
        assert entered.is_set()
        assert time.monotonic() - started < 0.7
        assert not response.closed.is_set()  # HTTP still owns the blocked operation.
    finally:
        release.set()
    assert response.closed.wait(1)
    assert not interpreted
    assert not isolated_usage


def test_cancel_stalled_stream_without_waiting_for_next_token(monkeypatch):
    release = threading.Event()
    cancelled = threading.Event()

    def lines():
        yield chunk({"content": "Partial"})
        release.wait(2)
        yield chunk({}, "stop")

    response = Response(lines=lines())
    monkeypatch.setattr(llm, "_post", lambda *a, **k: response)
    stream = llm.chat_stream("fixture", [], [], cancelled=cancelled.is_set)
    try:
        assert next(stream) == ("content", "Partial")
        cancelled.set()
        started = time.monotonic()
        with pytest.raises(llm.LLMCancelled):
            next(stream)
        assert time.monotonic() - started < 0.3
    finally:
        stream.close()
        release.set()
    assert response.closed.wait(1)


def test_cancellation_callback_stays_on_caller_thread_while_json_blocks(monkeypatch):
    release = threading.Event()
    entered = threading.Event()
    cancellation = threading.Event()
    response = Response()
    callbacks = []

    def read_json():
        entered.set()
        release.wait(2)
        return response.data

    def cancelled():
        callbacks.append(threading.get_ident())
        return cancellation.is_set()

    response.json = read_json
    monkeypatch.setattr(llm, "_post", lambda *a, **k: response)

    def trigger():
        assert entered.wait(1)
        cancellation.set()

    trigger_thread = threading.Thread(target=trigger)
    trigger_thread.start()
    started = time.monotonic()
    try:
        with pytest.raises(llm.LLMCancelled):
            llm.chat("fixture", [], [], cancelled=cancelled)
        assert time.monotonic() - started < 0.7
    finally:
        release.set()
        trigger_thread.join(1)
    assert response.closed.wait(1)
    assert set(callbacks) == {threading.get_ident()}


def test_continuous_slow_tokens_cannot_extend_deadline(monkeypatch):
    def lines():
        for _ in range(100):
            time.sleep(0.01)
            yield chunk({"reasoning": "still working"})
        yield chunk({}, "stop")

    response = Response(lines=lines())
    monkeypatch.setattr(llm, "_post", lambda *a, **k: response)
    events = []
    started = time.monotonic()
    with pytest.raises(llm.LLMDeadline):
        events.extend(llm.chat_stream("fixture", [], [], deadline=started + 0.08))
    assert time.monotonic() - started < 0.7
    assert events and all(kind == "reasoning" for kind, _ in events)
    assert response.closed.wait(1)


@pytest.mark.parametrize("control,exception", [
    ({"deadline": 0.0}, llm.LLMDeadline),
    ({"cancelled": lambda: True}, llm.LLMCancelled),
])
def test_expired_request_never_starts_http(monkeypatch, control, exception):
    monkeypatch.setattr(llm, "_post", lambda *a, **k: pytest.fail("unexpected request"))
    with pytest.raises(exception):
        llm.chat("fixture", [], [], **control)
    with pytest.raises(exception):
        list(llm.chat_stream("fixture", [], [], **control))


@pytest.mark.parametrize("stream", [False, True])
def test_reasoning_retry_closes_both_responses_and_recalculates_remaining_timeout(monkeypatch, stream):
    rejected = Response(status=400, text="reasoning is unsupported")
    accepted = Response(lines=[chunk({"content": "Complete"}, "stop"), "data: [DONE]"])
    calls = []

    def post(*args, **kwargs):
        calls.append((args[3], kwargs["include_reasoning"]))
        if len(calls) == 1:
            time.sleep(0.03)
            return rejected
        assert rejected.closed.is_set()
        return accepted

    monkeypatch.setattr(llm, "_post", post)
    deadline = time.monotonic() + 0.5
    if stream:
        events = list(llm.chat_stream("fixture", [], [], timeout=60, deadline=deadline))
        assert events[-1][0] == "done"
    else:
        assert llm.chat("fixture", [], [], timeout=60, deadline=deadline)["content"] == "Complete"
    assert accepted.closed.wait(1)
    assert calls[0][1] is True and calls[1][1] is False
    assert 0 < calls[1][0] < calls[0][0] <= 0.5


@pytest.mark.parametrize("stream", [False, True])
def test_http_failure_closes_response(monkeypatch, stream):
    response = Response(status=500)
    monkeypatch.setattr(llm, "_post", lambda *a, **k: response)
    with pytest.raises(llm.LLMError, match="temporarily unavailable"):
        if stream:
            list(llm.chat_stream("fixture", [], [], deadline=time.monotonic() + 1))
        else:
            llm.chat("fixture", [], [], deadline=time.monotonic() + 1)
    assert response.closed.wait(1)


def test_closing_consumer_releases_worker_when_queue_is_full(monkeypatch):
    response = Response(lines=(chunk({"content": "x"}) for _ in range(1000)))
    monkeypatch.setattr(llm, "_post", lambda *a, **k: response)
    stream = llm.chat_stream("fixture", [], [], deadline=time.monotonic() + 2)
    assert next(stream) == ("content", "x")
    stream.close()
    assert response.closed.wait(1)


def test_unguarded_call_preserves_synchronous_transport_and_closes_response(monkeypatch):
    response = Response()
    caller = threading.get_ident()

    def post(*args, **kwargs):
        assert threading.get_ident() == caller
        return response

    monkeypatch.setattr(llm, "_post", post)
    assert llm.chat("fixture", [], [])["content"] == "Complete"
    assert response.closed.is_set()


def test_connect_and_read_timeout_are_both_bounded(monkeypatch):
    from llm_api import openrouter_api

    calls = []
    monkeypatch.setattr(openrouter_api, "post_chat_completion", lambda *a, **k: calls.append(k))
    llm._post("fixture", [], [], 0.2, include_reasoning=True)
    assert calls[0]["read_timeout"] == 0.2
    assert calls[0]["connect_timeout"] == 0.2


def test_rejected_forced_tool_choice_is_retried_unforced(monkeypatch):
    """Some providers refuse tool_choice that names a tool; the same tools are
    offered unforced and the caller still checks which tool came back."""
    import json as _json
    from core.spend_tracker import spend_tracker

    class Response:
        def __init__(self, status, body):
            self.status_code, self._body = status, body
            self.text = _json.dumps(body)

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

        def json(self):
            return self._body

    sent = []

    def post(model, messages, tools, timeout, **kwargs):
        sent.append((kwargs["include_reasoning"], kwargs.get("tool_choice")))
        if kwargs.get("tool_choice") != "auto":
            return Response(400, {"error": {"metadata": {"raw": 'tool_choice: type "tool" and "any" are not supported for this model.'}}})
        call = {"id": "c1", "type": "function", "function": {"name": "save", "arguments": "{}"}}
        return Response(200, {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]})

    monkeypatch.setattr(spend_tracker, "is_over_cap", lambda: False)
    monkeypatch.setattr(llm, "_post", post)
    forced = {"type": "function", "function": {"name": "save"}}
    message = llm.chat("m", [{"role": "user", "content": "x"}], [{"type": "function", "function": {"name": "save"}}],
                       tool_choice=forced)
    assert [c["function"]["name"] for c in message["tool_calls"]] == ["save"]
    assert sent == [(True, forced), (True, "auto")]
