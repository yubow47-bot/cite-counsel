"""The harness: progressive plugin loading, the tool loop, settings, and the HTTP surface.

The model is scripted; no provider is called.
"""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from harness import core as harness_core, llm
from harness.app import create_app
from harness.core import Harness
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
                   default_enabled=True, api=AlphaApi(),
                   reply_guard=lambda text, ctx: "" if "1999" in text else text)
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
    assert "record__add_user_field" in script.calls[0]["tools"]    # harness-owned, always on
    assert script.calls[1]["tools"] == ["alpha__echo", "record__add_user_field", "record__new"]
    assert [b["type"] for b in blocks] == ["activity", "echo", "text"] and blocks[1]["plugin"] == "alpha"
    # The echo result was final: the model replies once more, with no tools on offer.
    assert len(script.calls) == 3 and script.calls[2]["tools"] == []
    assert harness.seen == [("echo", "hello there")]


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


def test_reply_guard_of_a_loaded_plugin_vetoes_prose(monkeypatch, harness):
    Script(monkeypatch, [call("load_plugin", name="alpha"), say("It was decided in 1999.")])
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "x")
    assert [b["type"] for b in blocks] == ["notice"] and "1999" not in str(blocks)


def test_repeated_failures_stop_early(monkeypatch, harness):
    script = Script(monkeypatch, [call("load_plugin", name="alpha")] + [call("alpha__missing")] * 10)
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "x")
    assert blocks[-1]["type"] == "notice" and len(script.calls) == 3


def test_step_limit(monkeypatch, harness):
    Script(monkeypatch, [call("load_plugin", name="alpha")] * 10)
    session, _ = harness.sessions.start()
    blocks = harness.run_turn(session, "x")
    assert blocks[-1]["type"] == "notice"


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


# 鈹€鈹€ The real plugins 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def test_builtin_plugins_are_discovered_and_valid():
    from harness.plugin import discover
    assert set(discover()) >= {"web", "deadlines"}   # plus the source plugins
    for plugin in discover().values():
        assert plugin.category in {"source", "extract", "function"}
        assert plugin.fact_patterns or plugin.category == "function"


# 鈹€鈹€ The record store and the built-in record tools 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _tool_reply(session):
    message = next(m for m in reversed(session.messages) if m.get("role") == "tool")
    return json.loads(message["content"])


def test_source_plugin_outputs_a_numbered_database_record(monkeypatch, harness):
    from core.tool_contracts import Field, Record

    class Source(BaseModel):
        query: str

    def find(ctx, p):
        return Result([Record("jurisprudence", {
            "style_of_cause": Field("R v Gladue", "database", source_id="a2aj:c1"),
            "neutral_citation": Field("[1999] 1 SCR 688", "database", source_id="a2aj:c1"),
        }, "a2aj", "c1")], [{"type": "card", "title": "cases"}])

    alpha = Plugin("alpha", "Alpha", "Finds records.", category="source",
                   tools=[Tool("find", "Find.", Source, find)], default_enabled=True)
    h = Harness({"alpha": alpha}, model="m", config_path=harness.config_path)
    Script(monkeypatch, [call("load_plugin", name="alpha"), call("alpha__find", query="gladue"), say("done")])
    session, _ = h.sessions.start()
    h.run_turn(session, "gladue")
    reply = _tool_reply(session)
    assert reply["objects"][0]["ref"] == "rec_1"
    assert reply["objects"][0]["fields"]["style_of_cause"] == "R v Gladue"
    assert "rec_1" in json.dumps(reply)


def test_extract_plugin_cannot_pass_off_database_fields(monkeypatch, harness):
    from core.tool_contracts import Field, Record

    class P(BaseModel):
        x: str = ""

    def steal(ctx, p):
        return Result([Record("page", {"title": Field("Header", "database", source_id="somewhere")},
                              "rogue", "r1")], [])

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
        return Result(Record("book", {"title": Field("Fabricated", "database", source_id="nowhere")},
                             "rogue", "r1"), [])

    alpha = Plugin("alpha", "Alpha", "Functions.", category="function",
                   tools=[Tool("mint", "Mint.", P, mint)], default_enabled=True)
    h = Harness({"alpha": alpha}, model="m", config_path=harness.config_path)
    Script(monkeypatch, [call("load_plugin", name="alpha"), call("alpha__mint"), say("done")])
    session, _ = h.sessions.start()
    h.run_turn(session, "x")
    reply = _tool_reply(session)
    assert reply["error"] == "contract violation"


def test_record_new_and_add_user_field_require_the_users_words(harness):
    from core.tool_contracts import Record

    session, _ = harness.sessions.start()
    session.messages.append({"role": "user", "content": "Edwards v Canada AG 1929，是判例，Privy Council，[1930] AC 124"})
    add_field, new_record = harness_core.BUILTIN_TOOLS
    result = harness._run_builtin(session, new_record, {"record_type": "jurisprudence"}, "record__new")[1]
    ref = result.content["ref"]
    assert result.content["fields"] == {}
    ok = harness._run_builtin(session, add_field,
                              {"ref": ref, "field": "neutral_citation", "text": "[1930] AC 124"},
                              "record__add_user_field")[1]
    assert ok.content["value"] == "[1930] AC 124"
    bad = harness._run_builtin(session, add_field,
                               {"ref": ref, "field": "court", "text": "Supreme Court of Canada"},
                               "record__add_user_field")[1]
    assert "error" in bad.content
    # The user field forms a new version; the blank record stays as it was.
    record = session.records.get(ok.content["ref"], Record)
    assert record.fields["neutral_citation"].origin == "user"
    assert session.records.get(ref, Record).fields == {}


def test_new_plugins_get_their_default_after_settings_were_saved(tmp_path):
    plugins, _ = make_plugins()
    path = tmp_path / "h.json"
    path.write_text(json.dumps({"enabled": [], "known": ["alpha"]}), encoding="utf-8")
    plugins["gamma"] = Plugin("gamma", "Gamma", "Installed later.", default_enabled=True)
    h = Harness(plugins, model="m", config_path=path)
    assert "alpha" not in h.config["enabled"]   # the user turned it off before
    assert "gamma" in h.config["enabled"]       # unknown then, default on


# ── Harness-level grounding (§5.4) and input hints (§5.2) ────────────


def test_harness_hides_only_the_paragraph_with_the_unsourced_fact(harness):
    Script(monkeypatch := None, []) if False else None
    session, _ = harness.sessions.start()
    session.messages.append({"role": "user", "content": "R v Gladue 1999"})
    session.messages.append({"role": "tool", "tool_call_id": "c1",
                             "content": '{"citation": "[1999] 1 SCR 688"}'})
    text = ("找到了 [1999] 1 SCR 688，请看卡片。\n"
            "另外它在 2002 SCC 10 里也被讨论过。")
    kept = harness._check_reply(session, text)
    assert "[1999] 1 SCR 688" in kept          # grounded: the tool result has it
    assert "2002 SCC 10" not in kept           # ungrounded paragraph hidden
    assert "请看卡片" in kept                   # clean paragraph survives


def test_check_applies_even_when_no_plugin_was_loaded(harness):
    session, _ = harness.sessions.start()
    session.messages.append({"role": "user", "content": "你好"})
    assert harness._check_reply(session, "It was decided in 1999.") == ""
    assert harness._check_reply(session, "没有可以查的插件。") != ""


def test_plugin_fact_patterns_apply_without_loading(harness):
    plugins, _ = make_plugins()
    from harness.core import Harness as H
    h = H(plugins, model="m", config_path=harness.config_path)
    h.plugins["alpha"].fact_patterns = (r"Bill\s+C-\d+",)  # enabled, not loaded
    session, _ = h.sessions.start()
    session.messages.append({"role": "user", "content": "查一下那个议案"})
    assert h._check_reply(session, "查到 Bill C-22。") == ""


def test_input_hints_flag_fixed_format_inputs():
    from harness.core import _input_hints
    hints = _input_hints("帮我查 10.1234/abc.def")
    assert "DOI" in hints
    assert _input_hints("gladue") == ""
    assert "ISBN" in _input_hints("这本书是 978-0-306-40615-7")
    assert "bill" in _input_hints("C-22 那个议案进展如何")
