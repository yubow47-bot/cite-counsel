"""The harness: progressive plugin loading, the tool loop, settings, and the HTTP surface.

The model is scripted; no provider is called.
"""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from harness import core as harness_core, llm
from harness.app import create_app
from harness.core import MAX_STEPS, Harness
from harness.plugin import Plugin, Result, Setting, Tool


class Echo(BaseModel):
    text: str


def make_plugins():
    seen = []

    def echo(ctx, p):
        seen.append(("echo", ctx.user_said(p.text)))
        return Result({"echoed": p.text}, [{"type": "echo", "text": p.text}], final=True)

    def peek(ctx, p):
        api, other = ctx.use("alpha")
        return Result({"alpha_state": api.count(other)})

    class AlphaApi:
        @staticmethod
        def count(ctx):
            return ctx.state.get("count", 0)

    alpha = Plugin("alpha", "Alpha", "Echoes text.", tools=[Tool("echo", "Echo.", Echo, echo)],
                   actions={"bump": lambda ctx, payload: (ctx.state.__setitem__("count", ctx.state.get("count", 0) + 1)
                                                          or Result({"count": ctx.state["count"]}))},
                   default_enabled=True, api=AlphaApi())
    beta = Plugin("beta", "Beta", "Peeks at alpha.", tools=[Tool("peek", "Peek.", Echo, peek)],
                  requires=("alpha",), default_enabled=False,
                  settings=[Setting("mode", "Mode", "choice", ("", "fast"), "")])
    return {"alpha": alpha, "beta": beta}, seen


class Script:
    """A fake model: returns queued messages and records what it was offered."""

    def __init__(self, monkeypatch, replies):
        self.replies, self.calls = list(replies), []
        monkeypatch.setattr(llm, "chat", self)

    def __call__(self, model, messages, tools, **kwargs):
        self.calls.append({"messages": messages, "tools": [t["function"]["name"] for t in tools]})
        return self.replies.pop(0)


class StreamScript:
    """The streaming counterpart to ``Script``: each queued reply becomes a
    ("reasoning", ...) delta (if it has one), a ("content", ...) delta (if
    it has one) -- both in one piece, exercising the same live-splitting
    path a real chunked stream would -- then the same ("done", message)
    ``chat_stream`` always ends with."""

    def __init__(self, monkeypatch, replies):
        self.replies, self.calls = list(replies), []
        monkeypatch.setattr(llm, "chat_stream", self)

    def __call__(self, model, messages, tools, **kwargs):
        self.calls.append({"messages": messages, "tools": [t["function"]["name"] for t in tools]})
        message = self.replies.pop(0)
        if message.get("reasoning"):
            yield "reasoning", message["reasoning"]
        if message.get("content"):
            yield "content", message["content"]
        yield "done", message


def _parse_sse(text: str) -> list[dict]:
    events = []
    for frame in text.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


def call(tool, **arguments):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c" + tool, "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments)}}]}


def say(text):
    return {"role": "assistant", "content": text}


@pytest.fixture
def harness(tmp_path):
    plugins, seen = make_plugins()
    h = Harness(plugins, model="test-model", config_path=tmp_path / "harness.json")
    h.seen = seen
    return h


def test_nothing_is_loaded_until_the_model_asks(monkeypatch, harness):
    script = Script(monkeypatch, [call("load_plugin", name="alpha"), call("alpha__echo", text="hello there"),
                                  say("Echoed it for you.")])
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "hello there")
    plugin_tools = [t for t in script.calls[0]["tools"] if not t.startswith("record__")]
    assert plugin_tools == ["load_plugin"]                        # only the catalogue
    assert "alpha: Echoes text." in script.calls[0]["messages"][0]["content"]
    assert "record__add_field" in script.calls[0]["tools"]        # harness-owned, always on
    assert script.calls[1]["tools"] == ["alpha__echo", "record__add_field", "record__new"]
    assert [b["type"] for b in blocks] == ["activity", "echo", "text"] and blocks[1]["plugin"] == "alpha"
    # The echo result was final: the model is told it is on screen, but keeps
    # its tools -- the request may need a next step; here it just replies.
    assert len(script.calls) == 3 and "alpha__echo" in script.calls[2]["tools"]
    assert "now on the user's screen" in script.calls[2]["messages"][0]["content"]
    assert harness.seen == [("echo", "hello there")]


def test_reasoning_becomes_a_thinking_block_on_every_step(monkeypatch, harness):
    Script(monkeypatch, [
        {**call("load_plugin", name="alpha"), "reasoning": "Need alpha for this."},
        {**call("alpha__echo", text="hi"), "reasoning": "Now call echo."},
        {**say("done"), "reasoning": "Nothing more to do."},
    ])
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "hi")
    thinking = [b["text"] for b in blocks if b["type"] == "thinking"]
    assert thinking == ["Need alpha for this.", "Now call echo.", "Nothing more to do."]
    # A model reply without "reasoning" (the common case) adds no such block.
    assert [b["type"] for b in blocks if b["type"] != "thinking"] == ["activity", "echo", "text"]


def test_think_tags_in_content_become_thinking_blocks(monkeypatch, harness):
    """GLM-style models put their chain of thought in think-tag sections
    inside content instead of the structured reasoning field; the reply the
    user reads must not carry the tags."""
    open_tag, close_tag = "<" + "think" + ">", "</" + "think" + ">"
    Script(monkeypatch, [
        {**call("load_plugin", name="alpha"),
         "content": open_tag + "Need alpha." + close_tag},
        {**call("alpha__echo", text="hi"),
         "content": open_tag + "Now echo." + close_tag + "Calling it."},
        say(open_tag + "a" + close_tag + "plain" + open_tag + "cut off"),
    ])
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "hi")
    thinking = [b["text"] for b in blocks if b["type"] == "thinking"]
    assert thinking == ["Need alpha.", "Now echo.", "a\n\ncut off"]
    final = next(b for b in blocks if b["type"] == "text")
    assert final["text"] == "plain"
    assert "think" + ">" not in str(blocks)


def test_loaded_plugins_stay_loaded_for_the_session(monkeypatch, harness):
    Script(monkeypatch, [call("load_plugin", name="alpha"), say("ok")])
    session, _ = harness.sessions.start()
    harness.run_turn(session, "first")
    script = Script(monkeypatch, [say("done")])
    harness.run_turn(session, "second")
    assert script.calls[0]["tools"][0] == "alpha__echo"          # no load_plugin left to offer


def test_disabled_plugins_are_listed_as_off_but_not_loadable(monkeypatch, harness):
    script = Script(monkeypatch, [call("load_plugin", name="beta"), say("no")])
    session, _ = harness.sessions.start()
    harness.run_turn(session, "x")
    offered = script.calls[0]["messages"][0]["content"]
    assert "disabled" in offered and "- beta: Peeks at alpha." in offered  # s5.1
    assert "beta" not in session.loaded
    tool_reply = json.loads(session.messages[2]["content"])
    assert tool_reply == {"error": "no such enabled plugin"}


def test_tools_of_unloaded_plugins_cannot_be_called(monkeypatch, harness):
    Script(monkeypatch, [call("alpha__echo", text="x"), say("sorry")])
    session, _ = harness.sessions.start()
    harness.run_turn(session, "x")
    assert json.loads(session.messages[2]["content"]) == {"error": "unknown or unloaded tool"}
    assert harness.seen == []


def test_invalid_arguments_go_back_to_the_model(monkeypatch, harness):
    Script(monkeypatch, [call("load_plugin", name="alpha"), call("alpha__echo", wrong=1), say("retry")])
    session, _ = harness.sessions.start()
    harness.run_turn(session, "x")
    reply = json.loads(session.messages[4]["content"])
    assert reply["error"] == "invalid arguments"


def test_a_reply_with_unsourced_facts_is_shown_and_annotated(monkeypatch, harness):
    """Nothing is hidden any more: the prose survives, and every unsourced
    fact in it is reported as such right on the text block."""
    Script(monkeypatch, [call("load_plugin", name="alpha"), say("It was decided in 1999.")])
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "x")
    assert [b["type"] for b in blocks] == ["text"]
    assert "1999" in blocks[-1]["text"]                       # shown, not vetoed
    assert any(f["fact"] == "1999" and f["verdict"] == "unsourced" for f in blocks[-1]["facts"])


def test_repeated_failures_stop_early(monkeypatch, harness):
    script = Script(monkeypatch, [call("load_plugin", name="alpha")] + [call("alpha__missing")] * 10)
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "x")
    assert blocks[-1]["type"] == "notice" and len(script.calls) == 3


def working_round():
    """One round that neither finishes nor fails: a good call beside a bad one."""
    ok, bad = call("alpha__echo", text="x")["tool_calls"][0], call("alpha__missing")["tool_calls"][0]
    return {"role": "assistant", "content": None, "tool_calls": [ok, {**bad, "id": "c2"}]}


def test_step_limit(monkeypatch, harness):
    script = Script(monkeypatch, [call("load_plugin", name="alpha")] + [working_round()] * (MAX_STEPS + 5))
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "x")
    assert blocks[-1]["type"] == "notice"
    assert len(script.calls) == MAX_STEPS + 1                     # the load round was free


def test_loading_plugins_is_bounded(monkeypatch, harness):
    Script(monkeypatch, [call("load_plugin", name="alpha")] * (MAX_STEPS + 10))
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "x")
    assert blocks[-1]["type"] == "notice"


def test_prompt_says_to_retry_before_asking(harness):
    session, _ = harness.sessions.start()
    prompt = harness._system_prompt(session)
    assert "search again in another form" in prompt and "record__new" in prompt


def test_action_notes_are_not_the_users_words(harness):
    session, _ = harness.sessions.start()
    harness.run_action(session, "alpha", "bump", {})
    ctx = harness.context(session, "alpha")
    assert session.messages[-1]["_note"] is True
    assert ctx.user_said("count") == ""                         # the note says "count"; the user did not
    assert "alpha" in session.loaded


def test_dependencies_must_be_declared(harness):
    session, _ = harness.sessions.start()
    harness.set_enabled("beta", True)
    api, ctx = harness.context(session, "beta").use("alpha")
    assert api.count(ctx) == 0
    with pytest.raises(ValueError):
        harness.context(session, "alpha").use("beta")


def test_enabling_pulls_in_requirements_and_disabling_protects_dependents(harness):
    harness.set_enabled("alpha", False)
    assert harness.set_enabled("beta", True) == ["beta", "alpha"]
    with pytest.raises(ValueError):
        harness.set_enabled("alpha", False)


def test_settings_persist_and_secrets_are_not_echoed(tmp_path):
    plugins, _ = make_plugins()
    plugins["beta"].settings.append(Setting("key", "Key", "secret"))
    h = Harness(plugins, model="m", config_path=tmp_path / "h.json")
    h.set_plugin_settings("beta", {"mode": "fast", "key": "s3cret"})
    with pytest.raises(ValueError):
        h.set_plugin_settings("beta", {"mode": "warp"})
    described = next(p for p in h.describe()["plugins"] if p["name"] == "beta")
    assert {"name": "key", "set": True}.items() <= next(s for s in described["settings"] if s["name"] == "key").items()
    assert "s3cret" not in json.dumps(h.describe())
    reloaded = Harness(make_plugins()[0], model="m", config_path=tmp_path / "h.json")
    assert reloaded.plugin_settings("beta")["mode"] == "fast"


# 鈹€鈹€ HTTP 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


@pytest.fixture
def client(harness):
    with TestClient(create_app(harness)) as c:
        yield c


def test_http_turn_creates_a_session_and_reuses_it(monkeypatch, client):
    Script(monkeypatch, [say("hi"), say("again")])
    first = client.post("/api/turns", json={"input": "hello"}).json()
    assert first["blocks"] == [{"type": "text", "text": "hi"}]
    ref = {"session_id": first["session"]["id"], "session_token": first["session"]["token"]}
    second = client.post("/api/turns", json={"input": "again", **ref}).json()
    assert "session" not in second  # same session, nothing new to hand out


def test_http_actions_need_a_live_session(client):
    assert client.post("/api/actions/alpha/bump", json={"payload": {}}).status_code == 409


def test_plugin_assets_only_for_enabled_plugins_with_ui(client, harness, tmp_path):
    assert client.get("/plugins/alpha/ui.js").status_code == 404        # no ui dir
    assert client.get("/plugins/beta/../../.env").status_code == 404


def test_settings_endpoints(client):
    config = client.get("/api/config").json()
    assert [p["name"] for p in config["plugins"]] == ["alpha", "beta"] and config["model"] == "test-model"
    updated = client.post("/api/settings/plugins/beta", json={"enabled": True}).json()
    assert next(p for p in updated["plugins"] if p["name"] == "beta")["enabled"] is True
    assert client.post("/api/settings/model", json={"model": "other/model"}).json()["model"] == "other/model"
    assert client.post("/api/settings/plugins/nope", json={"enabled": True}).status_code == 409


def test_cross_site_posts_are_refused(client):
    assert client.post("/api/turns", json={"input": "x"}, headers={"origin": "https://evil.example"}).status_code == 403


def test_http_turn_stream_sends_thinking_live_before_the_reply(monkeypatch, client):
    """The point of streaming: a thinking_delta event arrives, in its own
    frame, before the reply's text block -- not bundled into one response
    only once the whole turn is done."""
    StreamScript(monkeypatch, [{"role": "assistant", "content": "hi", "reasoning": "weighing how to greet them"}])
    response = client.post("/api/turns/stream", json={"input": "hello"})
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [e["type"] for e in events] == ["session", "thinking_delta", "block", "done"]
    assert events[1]["text"] == "weighing how to greet them"
    assert events[2]["block"] == {"type": "text", "text": "hi"}
    assert events[0]["session"]["id"]


def test_http_turn_stream_splits_a_leading_think_tag_live(monkeypatch, client):
    """A model with no structured reasoning field, only <think> tags in its
    content, still streams its thinking live -- peeled off chunk by chunk,
    not just recovered once the message is complete."""
    StreamScript(monkeypatch, [{"role": "assistant", "content": "<think>weighing options</think>the answer"}])
    events = _parse_sse(client.post("/api/turns/stream", json={"input": "hello"}).text)
    assert "".join(e["text"] for e in events if e["type"] == "thinking_delta") == "weighing options"
    assert [e["block"] for e in events if e["type"] == "block"] == [{"type": "text", "text": "the answer"}]


def test_http_turn_stream_runs_tool_calls_like_the_json_endpoint(monkeypatch, client):
    """The streamed turn takes the same steps as run_turn -- load a plugin,
    call its tool, reply -- just handed to the caller one block at a time."""
    StreamScript(monkeypatch, [call("load_plugin", name="alpha"), call("alpha__echo", text="hi"), say("done")])
    events = _parse_sse(client.post("/api/turns/stream", json={"input": "echo hi"}).text)
    block_types = [e["block"]["type"] for e in events if e["type"] == "block"]
    assert block_types == ["activity", "echo", "text"]


def test_an_attachment_filename_is_not_the_users_own_words(monkeypatch, harness):
    """A fact sitting in an uploaded file's NAME (a page range, a year) must
    not be able to pass itself off as something the user typed -- it is the
    harness's own note about the upload, not the user's words. Regression:
    the note used to be spliced into the same user message as the real
    input, so user_said() (and therefore record__add_field's fallback
    source-matching) found it there and called it "the user's own words"."""
    Script(monkeypatch, [say("noted")])
    session, _ = harness.sessions.start()
    session.attachments["att_1"] = {"path": "/tmp/x.pdf",
                                    "name": "Ch06 Data on Law & Society (pp.161-189).pdf"}
    harness.run_turn(session, "process it", attachments=["att_1"])
    ctx = harness.context(session, "alpha")
    assert ctx.user_said("161-189") == ""            # not something the user said
    assert ctx.user_said("process it") == "process it"   # the real input still is
    # The note is still visible to the model in history, just not as a user statement.
    history_text = " ".join(m.get("content") or "" for m in session.messages)
    assert "161-189" in history_text


# 鈹€鈹€ The real plugins 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def test_builtin_plugins_are_discovered_and_valid():
    from harness.plugin import discover
    assert set(discover()) >= {"web", "deadlines"}   # plus the source plugins
    for plugin in discover().values():
        assert plugin.category in {"source", "extract", "function"}
        for pattern in plugin.fact_patterns:
            import re as _re
            assert _re.compile(pattern)


# 鈹€鈹€ The record store and the built-in record tools 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _tool_reply(session):
    message = next(m for m in reversed(session.messages) if m.get("role") == "tool")
    return json.loads(message["content"])


def test_source_plugin_outputs_a_numbered_database_record(monkeypatch, harness):
    from core.tool_contracts import Field, Record

    class Source(BaseModel):
        query: str

    def find(ctx, p):
        record = Record("jurisprudence", {
            "style_of_cause": Field("R v Gladue", "database", source_id="a2aj:c1"),
            "neutral_citation": Field("[1999] 1 SCR 688", "database", source_id="a2aj:c1"),
        }, "a2aj", "c1")
        ref = ctx.save(record)
        return Result({"records": [{"ref": ref}]}, [{"type": "card", "title": "cases"}])

    alpha = Plugin("alpha", "Alpha", "Finds records.", category="source",
                   tools=[Tool("find", "Find.", Source, find)], default_enabled=True)
    h = Harness({"alpha": alpha}, model="m", config_path=harness.config_path)
    Script(monkeypatch, [call("load_plugin", name="alpha"), call("alpha__find", query="gladue"), say("done")])
    session, _ = h.sessions.start()
    h.run_turn(session, "gladue")
    reply = _tool_reply(session)
    assert reply["records"][0]["ref"] == "rec_1"
    assert session.records.get("rec_1").fields["style_of_cause"].value == "R v Gladue"
    assert reply["records"][0]["ref"] in json.dumps(reply)


def test_extract_plugin_cannot_pass_off_database_fields(monkeypatch, harness):
    from core.tool_contracts import Field, Record

    class P(BaseModel):
        x: str = ""

    def steal(ctx, p):
        ctx.save(Record("page", {"title": Field("Header", "database", source_id="somewhere")},
                        "rogue", "r1"))
        return Result({"done": True}, [])

    alpha = Plugin("alpha", "Alpha", "Extracts.", category="extract",
                   tools=[Tool("grab", "Grab.", P, steal)], default_enabled=True)
    h = Harness({"alpha": alpha}, model="m", config_path=harness.config_path)
    Script(monkeypatch, [call("load_plugin", name="alpha"), call("alpha__grab"), say("done")])
    session, _ = h.sessions.start()
    h.run_turn(session, "x")
    reply = _tool_reply(session)
    assert reply["error"] == "contract violation"
    assert "database" in reply["details"]


def test_function_plugin_cannot_mint_a_record(monkeypatch, harness):
    from core.tool_contracts import Field, Record

    class P(BaseModel):
        x: str = ""

    def mint(ctx, p):
        ctx.save(Record("book", {"title": Field("Fabricated", "database", source_id="nowhere")},
                        "rogue", "r1"))
        return Result({"done": True}, [])

    alpha = Plugin("alpha", "Alpha", "Functions.", category="function",
                   tools=[Tool("mint", "Mint.", P, mint)], default_enabled=True)
    h = Harness({"alpha": alpha}, model="m", config_path=harness.config_path)
    Script(monkeypatch, [call("load_plugin", name="alpha"), call("alpha__mint"), say("done")])
    session, _ = h.sessions.start()
    h.run_turn(session, "x")
    reply = _tool_reply(session)
    assert reply["error"] == "contract violation"


def test_record_new_and_add_field_stamp_the_true_origin(harness):
    from core.tool_contracts import Record

    session, _ = harness.sessions.start()
    session.messages.append({"role": "user", "content": "Edwards v Canada AG 1929，是判例，Privy Council，[1930] AC 124"})
    add_field, new_record = harness_core.BUILTIN_TOOLS
    result = harness._run_builtin(session, new_record, {"record_type": "jurisprudence"}, "record__new")[1]
    ref = result.content["ref"]
    assert result.content["fields"] == {}
    ok = harness._run_builtin(session, add_field,
                              {"ref": ref, "field": "neutral_citation", "value": "[1930] AC 124"},
                              "record__add_field")[1]
    assert ok.content["value"] == "[1930] AC 124"
    assert ok.content["origin"] == "user"                     # the user wrote it
    # The user field forms a new version; the object stored under the
    # original ref is untouched (still blank) --
    assert session.records._objects[ref].fields == {}
    # -- but a caller that still cites that old ref reaches the latest
    # version instead of a dead end (a model citing a stale ref after the
    # record moved on, or two add_field calls batched in one round).
    record = session.records.get(ref, Record)                 # the chain's latest version
    assert record.fields["neutral_citation"].origin == "user"
    assert session.records.get(ref, Record) is record


def test_add_field_copies_from_a_named_record_field(harness):
    """The value is read directly off the named field -- no string matching,
    so origin and source_id travel with the copy untouched."""
    from core.tool_contracts import Field, Record

    session, _ = harness.sessions.start()
    add_field, new_record = harness_core.BUILTIN_TOOLS
    ref = harness._run_builtin(session, new_record, {"record_type": "jurisprudence"}, "record__new")[1].content["ref"]
    session.records.put(Record("jurisprudence", {"court": Field("Supreme Court of Canada", "database",
                                                                source_id="a2aj:c1")}, "a2aj", "c1"))
    copied = harness._run_builtin(session, add_field,
                                  {"ref": ref, "field": "court", "from_ref": "rec_2", "from_field": "court"},
                                  "record__add_field")[1]
    assert copied.content["origin"] == "database" and copied.content["from_ref"] == "rec_2"
    record = session.records.get(ref, Record)
    assert record.fields["court"].source_id == "a2aj:c1"


def test_add_field_labels_a_source_and_never_refuses_for_lack_of_one(harness):
    """The source is a label, not a gate: a value read out of a page's text
    inherits the page's origin, a value the user wrote is theirs, and a value
    found nowhere is still written -- marked model-supplied, unverified."""
    from core.tool_contracts import Field, Record

    session, _ = harness.sessions.start()
    add_field, new_record = harness_core.BUILTIN_TOOLS
    ref = harness._run_builtin(session, new_record, {"record_type": "jurisprudence"}, "record__new")[1].content["ref"]
    session.records.put(Record("website", {
        "title": Field("Donoghue v Stevenson", "extracted", source_id="https://case.report"),
        "text": Field("The House of Lords decided the appeal on 26 May 1932, [1932] AC 562.",
                      "extracted", source_id="https://case.report"),
    }, "web", "https://case.report"))

    def write(**params):
        return harness._run_builtin(session, add_field, {"ref": ref, **params}, "record__add_field")[1]

    # A fact read out of the page's text, with the page named.
    court = write(field="court", value="House of Lords", from_ref="rec_2", from_field="text")
    assert court.content["origin"] == "extracted" and court.content["from_ref"] == "rec_2"
    # The same, without naming the page: the harness finds it anyway.
    year = write(field="year", value="[1932] AC 562")
    assert year.content["origin"] == "extracted" and year.content["from_ref"] == "rec_2"
    # A value from nowhere is written, labelled model -- not refused.
    judge = write(field="judge", value="Lord Atkin")
    assert "error" not in judge.content and judge.content["origin"] == "model"
    assert "unverified" in judge.content["note"]
    # A wrong from_ref / from_field is a hint that missed, not an error.
    missed = write(field="place", value="London", from_ref="rec_99", from_field="nope")
    assert "error" not in missed.content and missed.content["origin"] == "model"
    # The user's words are theirs.
    session.messages.append({"role": "user", "content": "法院是 Court of Session"})
    lower = write(field="lower_court", value="Court of Session")
    assert lower.content["origin"] == "user"
    record = session.records.get(ref, Record)
    assert {name: f.origin for name, f in record.fields.items()} == {
        "court": "extracted", "year": "extracted", "judge": "model", "place": "model", "lower_court": "user"}
    # The only refusal: nothing to write at all.
    empty = write(field="date", from_ref="rec_2", from_field="date")
    assert "Nothing to write" in empty.content["error"]


def test_copying_never_fails_on_formatting(harness):
    """Copying is a field lookup, not a text match: a date stored as ISO and
    rendered as words is still one value."""
    from core.tool_contracts import Field, Record

    session, _ = harness.sessions.start()
    add_field, new_record = harness_core.BUILTIN_TOOLS
    ref = harness._run_builtin(session, new_record, {"record_type": "website"}, "record__new")[1].content["ref"]
    session.records.put(Record("website", {"date": Field("2026-09-05", "extracted", source_id="https://x"),
                                           "title": Field("A Page", "extracted", source_id="https://x")},
                              "web", "https://x"))
    copied = harness._run_builtin(session, add_field,
                                  {"ref": ref, "field": "date", "from_ref": "rec_2", "from_field": "date"},
                                  "record__add_field")[1]
    assert copied.content["origin"] == "extracted"
    assert copied.content["value"] == "2026-09-05"            # the stored value, not a retyped one


def test_record_new_wants_a_web_search_first_when_web_is_enabled(tmp_path):
    """Giving up on the databases is only allowed after the web was tried."""
    from harness.plugin import discover

    h = Harness(discover(), model="m", config_path=tmp_path / "h.json")
    h.set_enabled("web", True)
    session, _ = h.sessions.start()
    new_record = harness_core.BUILTIN_TOOLS[1]
    refused = h._run_builtin(session, new_record, {"record_type": "jurisprudence"}, "record__new")[1]
    assert "web__search" in refused.content["error"]
    assert session.records.all() == []                        # nothing was created
    # One real search attempt -- even one that finds nothing -- opens the gate.
    session.web_search_used = True
    result = h._run_builtin(session, new_record, {"record_type": "jurisprudence"}, "record__new")[1]
    assert result.content["ref"] == "rec_1"


def test_a_record_built_by_copying_from_a_page_cites_with_the_page_provenance(tmp_path):
    """The fetched-page workflow end to end: web.fetch stores date/title/url
    as named fields, so the model copies them by reference -- no free text,
    no substring guessing."""
    from core.tool_contracts import Field, Record
    from harness.plugin import discover

    h = Harness(discover(), model="m", config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    add_field, new_record = harness_core.BUILTIN_TOOLS
    ref = h._run_builtin(session, new_record, {"record_type": "foreign"}, "record__new")[1].content["ref"]
    session.records.put(Record("website", {
        "title": Field("Donoghue v Stevenson Case Resources", "extracted", source_id="https://case.report"),
        "date": Field("26 May 1932", "extracted", source_id="https://case.report"),
        "url": Field("https://case.report", "extracted", source_id="https://case.report"),
    }, "web", "https://case.report"))
    last = None
    for field in ("date", "title"):
        last = h._run_builtin(session, add_field,
                              {"ref": ref, "field": field, "from_ref": "rec_2", "from_field": field},
                              "record__add_field")[1]
        assert last.content["origin"] == "extracted" and last.content["from_ref"] == "rec_2"
    assert last.content["value"] == "Donoghue v Stevenson Case Resources"


def test_a_model_value_is_never_evidence_for_the_next_copy(tmp_path):
    """A model-origin field stored by a plugin (or an old session file) never
    becomes a source: Store keeps it out of the evidence set entirely."""
    from core.tool_contracts import Field, Record
    from harness.plugin import discover

    h = Harness(discover(), model="m", config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    ctx = h.context(session, "mcgill")
    with pytest.raises(Exception) as exc:
        ctx.save(Record("jurisprudence", {"court": Field("The Court of Fairy Tales", "model")}, "user", "blank"))
    assert "不能产出" in str(exc.value)                        # function plugins cannot mint records at all
    # Even the harness-built-in path only stores what it can source.
    add_field, new_record = harness_core.BUILTIN_TOOLS
    ref = h._run_builtin(session, new_record, {"record_type": "jurisprudence"}, "record__new")[1].content["ref"]
    refused = h._run_builtin(session, add_field,
                             {"ref": ref, "field": "court", "user_said": "The Court of Fairy Tales"},
                             "record__add_field")[1]
    assert "error" in refused.content and refused.content.get("origin") is None
    assert "court" not in session.records.get(ref, Record).fields


def test_two_add_field_calls_batched_in_one_round_both_land(harness):
    """A model that issues both field-writing calls before either result
    comes back can only pass the SAME starting ref to both -- the harness
    still merges them, because the second call resolves that stale ref to
    the version the first call just produced (calls in a round run in
    sequence, not concurrently)."""
    from core.tool_contracts import Record

    session, _ = harness.sessions.start()
    session.messages.append({"role": "user", "content": "Zzyx v Qwerty, 2099 FAKE 999"})
    add_field, new_record = harness_core.BUILTIN_TOOLS
    blank_ref = harness._run_builtin(session, new_record, {"record_type": "jurisprudence"},
                                     "record__new")[1].content["ref"]

    first = harness._run_builtin(session, add_field,
                                 {"ref": blank_ref, "field": "style_of_cause", "value": "Zzyx v Qwerty"},
                                 "record__add_field")[1]
    # Issued against the SAME blank_ref as `first` -- as a model would when
    # both calls are queued in one assistant turn, before either result exists.
    second = harness._run_builtin(session, add_field,
                                  {"ref": blank_ref, "field": "neutral_citation", "value": "2099 FAKE 999"},
                                  "record__add_field")[1]

    merged = session.records.get(second.content["ref"], Record)
    assert merged.fields["style_of_cause"].value == "Zzyx v Qwerty"
    assert merged.fields["neutral_citation"].value == "2099 FAKE 999"
    assert merged.fields["style_of_cause"].origin == "user" and merged.fields["neutral_citation"].origin == "user"
    # The stale starting ref both calls were given now reaches the merged
    # version -- the second call's forwarding overtakes the first's.
    assert session.records.get(blank_ref, Record) is merged


def test_new_plugins_get_their_default_after_settings_were_saved(tmp_path):
    plugins, _ = make_plugins()
    path = tmp_path / "h.json"
    path.write_text(json.dumps({"enabled": [], "known": ["alpha"]}), encoding="utf-8")
    plugins["gamma"] = Plugin("gamma", "Gamma", "Installed later.", default_enabled=True)
    h = Harness(plugins, model="m", config_path=path)
    assert "alpha" not in h.config["enabled"]   # the user turned it off before
    assert "gamma" in h.config["enabled"]       # unknown then, default on


# ── Harness-level grounding (§5.4) and input hints (§5.2) ────────────


def test_reply_facts_carry_their_source_and_stay_visible(harness):
    """Annotation, not hiding: the prose survives untouched; a fact that
    traces to the session carries its source, a fact that traces nowhere is
    reported as unsourced."""
    session, _ = harness.sessions.start()
    session.messages.append({"role": "user", "content": "R v Gladue 1999"})
    session.messages.append({"role": "tool", "tool_call_id": "c1",
                             "content": '{"citation": "[1999] 1 SCR 688"}'})
    text = ("找到了 [1999] 1 SCR 688，请看卡片。\n"
            "另外它在 2002 SCC 10 里也被讨论过。")
    facts = harness._annotate_reply(session, text)
    sourced = {f["fact"]: f for f in facts if f["verdict"] == "sourced"}
    unsourced = {f["fact"] for f in facts if f["verdict"] == "unsourced"}
    assert "[1999] 1 SCR 688" in sourced                       # grounded: the tool result has it
    assert "2002 SCC 10" in unsourced
    assert "请看卡片" in text and "2002 SCC 10" in text          # nothing was removed


def test_a_fact_from_a_stored_record_links_to_it(harness):
    from core.tool_contracts import Field, Record

    session, _ = harness.sessions.start()
    session.records.put(Record("jurisprudence", {"pinpoint": Field("at para 12", "database",
                                                                  source_id="a2aj:c1")}, "a2aj", "c1"))
    facts = harness._annotate_reply(session, "判决在 at para 12 讲了这一点，又在 at para 12 重申。")
    entry = next(f for f in facts if f["fact"] == "para 12")
    assert entry["verdict"] == "sourced" and entry["kind"] == "record"
    assert entry["ref"] == "rec_1" and entry["field"] == "pinpoint"
    assert entry["origin"] == "database" and entry["source_id"] == "a2aj:c1"
    assert entry["excerpt"] == "at para 12"
    assert len(entry["spans"]) == 2                            # both occurrences


def test_an_unsourced_fact_is_annotated_without_a_source(harness):
    session, _ = harness.sessions.start()
    session.messages.append({"role": "user", "content": "你好"})
    facts = harness._annotate_reply(session, "It was decided in 1999.")
    assert facts and all(f["verdict"] == "unsourced" and f["ref"] is None for f in facts)
    assert harness._annotate_reply(session, "没有可以查的插件。") == []


def test_plugin_fact_patterns_apply_without_loading(harness):
    plugins, _ = make_plugins()
    from harness.core import Harness as H
    h = H(plugins, model="m", config_path=harness.config_path)
    h.plugins["alpha"].fact_patterns = (r"Bill\s+C-\d+",)  # enabled, not loaded
    session, _ = h.sessions.start()
    session.messages.append({"role": "user", "content": "查一下那个议案"})
    facts = h._annotate_reply(session, "查到 Bill C-22。")
    assert facts and facts[0]["verdict"] == "unsourced" and facts[0]["fact"] == "Bill C-22"


def test_input_hints_flag_fixed_format_inputs():
    from harness.core import _input_hints
    hints = _input_hints("帮我查 10.1234/abc.def")
    assert "DOI" in hints
    assert _input_hints("gladue") == ""
    assert "ISBN" in _input_hints("这本书是 978-0-306-40615-7")
    assert "bill" in _input_hints("C-22 那个议案进展如何")


def test_a_tool_call_written_as_text_is_recovered():
    leaked = {"role": "assistant", "content":
              "<tool_call>a2aj__find_case<arg_key>query</arg_key><arg_value>2016</arg_value></tool_call>"}
    message = llm._recover_leaked_calls(leaked)
    assert message["content"] == ""
    call = message["tool_calls"][0]["function"]
    assert call["name"] == "a2aj__find_case"
    assert json.loads(call["arguments"]) == {"query": "2016"}      # a scalar stays a string
    plain = {"role": "assistant", "content": "no markup here"}
    assert llm._recover_leaked_calls(plain) is plain


def test_a_leaked_call_runs_instead_of_being_shown(monkeypatch, harness):
    leaked = {"role": "assistant", "content":
              "<tool_call>load_plugin<arg_key>name</arg_key><arg_value>alpha</arg_value></tool_call>"}
    Script(monkeypatch, [llm._recover_leaked_calls(leaked), say("done")])
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "x")
    assert "alpha" in session.loaded
    assert not any("<tool_call>" in str(b) for b in blocks)


def test_a_final_result_does_not_end_a_request_that_needs_more(monkeypatch, harness):
    """Extracting a file is final (showable), but the user asked for a
    citation: the model must still be able to take that next step."""
    script = Script(monkeypatch, [call("load_plugin", name="alpha"), call("alpha__echo", text="x"),
                                  call("alpha__echo", text="x"), say("done")])
    session, _ = harness.sessions.start()
    harness.run_turn(session, "x")
    assert len(script.calls) == 4                                 # a second tool round after a final one
    assert harness.seen == [("echo", "x"), ("echo", "x")]


def test_a_long_clean_reply_is_shown_in_full(monkeypatch, harness):
    """A brainstorm runs past a few sentences. With nothing unsourced in it,
    none of it is hidden -- length alone is not a reason to silence it."""
    paragraph = "一个 citator 插件可以告诉律师某个判例现在是否仍然有效，这是引注可靠性最缺的一环。"
    long_reply = "\n\n".join([paragraph] * 20)
    Script(monkeypatch, [say(long_reply)])
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "你 brainstorm 一下")
    assert len(long_reply) > 600
    assert [b["type"] for b in blocks] == ["text"] and blocks[0]["text"].count("citator") == 20
