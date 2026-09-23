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
    assert script.calls[0]["tools"] == ["load_plugin"]            # only the catalogue
    assert "alpha: Echoes text." in script.calls[0]["messages"][0]["content"]
    assert script.calls[1]["tools"] == ["alpha__echo"]           # loaded on request
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
    assert script.calls[0]["tools"] == ["alpha__echo"]          # no load_plugin left to offer


def test_disabled_plugins_are_not_offered(monkeypatch, harness):
    script = Script(monkeypatch, [call("load_plugin", name="beta"), say("no")])
    session, _ = harness.sessions.start()
    harness.run_turn(session, "x")
    offered = script.calls[0]["messages"][0]["content"]
    assert "beta" not in offered and "beta" not in session.loaded
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


# ── HTTP ─────────────────────────────────────────────────────────────


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


# ── The real plugins ─────────────────────────────────────────────────


def test_builtin_plugins_are_discovered_and_valid():
    from harness.plugin import discover
    assert set(discover()) == {"web", "deadlines"}


def test_new_plugins_get_their_default_after_settings_were_saved(tmp_path):
    plugins, _ = make_plugins()
    path = tmp_path / "h.json"
    path.write_text(json.dumps({"enabled": [], "known": ["alpha"]}), encoding="utf-8")
    plugins["gamma"] = Plugin("gamma", "Gamma", "Installed later.", default_enabled=True)
    h = Harness(plugins, model="m", config_path=path)
    assert "alpha" not in h.config["enabled"]   # the user turned it off before
    assert "gamma" in h.config["enabled"]       # unknown then, default on
