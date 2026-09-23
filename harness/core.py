"""Plugins, their settings, and the conversation loop.

The loop is the whole of the harness's intelligence:

1. The model sees the enabled plugins as one line each, plus the tools of the
   plugins it has already loaded in this session.
2. It calls ``load_plugin`` for what this request needs; that plugin's tools
   and instructions join the next step. Nothing is active by default.
3. Tool results go back to the model as compact JSON and to the user as the
   plugin's own blocks. When every result in a step says it already answers
   the user (``final``), the model gets one last call without tools to reply
   in words, so the turn cannot spin into more tool calls.

The one rule the harness itself enforces: what the user is shown as a result
comes from tool blocks. The model's prose is a caption, and each loaded plugin
may veto it (``reply_guard``).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from harness import llm
from harness.plugin import Plugin, Result, discover
from harness.session import HISTORY_LIMIT, Context, Session, SessionStore

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / ".chatbox-runtime" / "harness.json"
MAX_STEPS = 6
TOOL_CONTENT_LIMIT = 6000
SEP = "__"  # model-facing tool names: plugin__tool (dots are not allowed there)
FOLLOW_UP = (
    "\n\nThe tool results are now on the user's screen. Reply to the user in one to three sentences: "
    "what you found or did, and what they can do next (e.g. which candidate is most likely what they "
    "meant, and that they can pick it). You may mention facts that appear in the tool results; do not "
    "add any that do not. Never write out a full citation yourself: the card shows the exact text, so "
    "refer to it by name and date only.")


def _strip_private(message: dict) -> dict:
    return {k: v for k, v in message.items() if not k.startswith("_")}


class Harness:
    def __init__(self, plugins: dict[str, Plugin] | None = None, *, model: str = "",
                 config_path: Path = CONFIG_PATH):
        self.plugins = plugins if plugins is not None else discover()
        self.config_path = config_path
        self.sessions = SessionStore()
        self._config_lock = threading.Lock()
        self.config = self._load_config(model)

    # ── Configuration (the settings bar) ─────────────────────────────

    def _load_config(self, model: str) -> dict:
        config = {"model": model, "enabled": [n for n, p in self.plugins.items() if p.default_enabled],
                  "plugin_settings": {}}
        try:
            saved = json.loads(self.config_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                if isinstance(saved.get("model"), str) and saved["model"].strip():
                    config["model"] = saved["model"].strip()
                if isinstance(saved.get("enabled"), list):
                    # Plugins installed since the last save get their own default.
                    known = set(saved.get("known") or saved["enabled"])
                    config["enabled"] = [n for n in saved["enabled"] if n in self.plugins] + [
                        n for n, p in self.plugins.items() if n not in known and p.default_enabled]
                if isinstance(saved.get("plugin_settings"), dict):
                    config["plugin_settings"] = saved["plugin_settings"]
        except (OSError, ValueError):
            pass
        config["enabled"] = self._with_requirements(config["enabled"])
        return config

    def _save_config(self) -> None:
        try:
            self.config_path.parent.mkdir(exist_ok=True)
            saved = {**self.config, "known": sorted(self.plugins)}
            self.config_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("Could not persist harness settings")

    def _with_requirements(self, names: list[str]) -> list[str]:
        result, pending = [], list(names)
        while pending:
            name = pending.pop(0)
            if name not in result and name in self.plugins:
                result.append(name)
                pending.extend(self.plugins[name].requires)
        return result

    def set_model(self, model: str) -> None:
        with self._config_lock:
            self.config["model"] = model
            self._save_config()

    def set_enabled(self, name: str, enabled: bool) -> list[str]:
        if name not in self.plugins:
            raise ValueError("没有这个插件。")
        with self._config_lock:
            current = [n for n in self.config["enabled"] if n != name]
            if enabled:
                current.append(name)
            else:
                dependents = [n for n in current if name in self.plugins[n].requires]
                if dependents:
                    raise ValueError("先停用依赖它的插件：" + "、".join(self.plugins[n].title for n in dependents))
            self.config["enabled"] = self._with_requirements(current)
            self._save_config()
            return self.config["enabled"]

    def plugin_settings(self, name: str) -> dict:
        plugin = self.plugins[name]
        saved = self.config["plugin_settings"].get(name) or {}
        return {s.name: saved.get(s.name, s.default) for s in plugin.settings}

    def set_plugin_settings(self, name: str, values: dict) -> None:
        plugin = self.plugins.get(name)
        if plugin is None:
            raise ValueError("没有这个插件。")
        known = {s.name: s for s in plugin.settings}
        with self._config_lock:
            saved = dict(self.config["plugin_settings"].get(name) or {})
            for key, value in values.items():
                setting = known.get(key)
                if setting is None:
                    raise ValueError(f"插件没有设置项 {key}。")
                if setting.kind == "bool":
                    value = bool(value)
                elif setting.kind == "choice":
                    if value not in setting.choices:
                        raise ValueError(f"{setting.label} 的取值无效。")
                elif not isinstance(value, str) or len(value) > 500:
                    raise ValueError(f"{setting.label} 必须是 500 字以内的文本。")
                saved[key] = value
            self.config["plugin_settings"][name] = saved
            self._save_config()

    def describe(self) -> dict:
        """The settings bar's view: no secret values, only whether they are set."""
        plugins = []
        for plugin in self.plugins.values():
            values = self.plugin_settings(plugin.name)
            plugins.append({
                "name": plugin.name, "title": plugin.title, "description": plugin.description,
                "enabled": plugin.name in self.config["enabled"], "requires": list(plugin.requires),
                "tools": [{"name": t.name, "description": t.description} for t in plugin.tools],
                "ui": plugin.ui is not None,
                "settings": [{"name": s.name, "label": s.label, "kind": s.kind, "choices": list(s.choices),
                              "labels": s.labels, "help": s.help,
                              **({"set": bool(values.get(s.name))} if s.kind == "secret"
                                 else {"value": values.get(s.name)})}
                             for s in plugin.settings],
            })
        return {"model": self.config["model"], "llm_configured": llm.configured(), "plugins": plugins}

    # ── Context and dependencies ─────────────────────────────────────

    def context(self, session: Session, name: str) -> Context:
        return Context(session, name, self, self.plugin_settings(name))

    def dependency(self, caller: str, name: str, session: Session):
        if name not in self.plugins[caller].requires:
            raise ValueError(f"插件 {caller} 没有声明依赖 {name}")
        return self.plugins[name].api, self.context(session, name)

    def enabled(self) -> list[Plugin]:
        return [self.plugins[n] for n in self.config["enabled"]]

    # ── The loop ─────────────────────────────────────────────────────

    def _system_prompt(self, session: Session, follow_up: bool = False) -> str:
        enabled = self.enabled()
        loaded = [p for p in enabled if p.name in session.loaded]
        waiting = [p for p in enabled if p.name not in session.loaded]
        lines = [
            "You are the assistant of Cite Counsel, a workspace for legal research and writing. "
            "Reply in the language of the user's latest message (Simplified Chinese if they write Chinese, "
            "or if unsure), in one to three short sentences.",
            "You act only through plugins. A plugin's tools become available after you call "
            "load_plugin; load only what this request needs.",
            "Results the tools show the user are authoritative. Do not restate or alter their facts "
            "in your reply, and never invent facts, sources, citations, dates or numbers. If no "
            "plugin can do what is asked, say so plainly.",
            "Tool arguments that stand for something the user wrote must be copied from the user's words.",
        ]
        latest = next((t for t in reversed(session.user_texts()) if t.strip()), "")
        # A bare case name ("gladue") says nothing about the user's language; the
        # interface is Chinese, so that is the default.
        language = ("Reply in Simplified Chinese." if any("一" <= ch <= "鿿" for ch in latest)
                    or len(latest.split()) < 6 else "Reply in the language of the user's latest message.")
        if waiting:
            lines += ["", "Plugins you can load:"] + [f"- {p.name}: {p.description}" for p in waiting]
        if loaded:
            lines += ["", "Loaded plugins:"]
            for plugin in loaded:
                lines.append(f"- {plugin.name}: {plugin.description}")
                if plugin.instructions:
                    lines.append("  " + plugin.instructions.replace("\n", "\n  "))
        # Last, where the model weighs it most.
        return "\n".join(lines) + (FOLLOW_UP if follow_up else "") + "\n\n" + language

    def _tool_specs(self, session: Session) -> list[dict]:
        enabled = self.enabled()
        waiting = [p.name for p in enabled if p.name not in session.loaded]
        specs = []
        if waiting:
            specs.append({"type": "function", "function": {
                "name": "load_plugin", "description": "Make a plugin's tools available for this conversation.",
                "parameters": {"type": "object", "properties": {"name": {"type": "string", "enum": waiting}},
                               "required": ["name"]}}})
        for plugin in enabled:
            if plugin.name not in session.loaded:
                continue
            for tool in plugin.tools:
                schema = tool.params.model_json_schema()
                schema.pop("title", None)
                specs.append({"type": "function", "function": {
                    "name": plugin.name + SEP + tool.name, "description": tool.description, "parameters": schema}})
        return specs

    def _history(self, session: Session) -> list[dict]:
        messages = session.messages[-HISTORY_LIMIT:]
        # Never start inside a tool exchange: begin at a user message.
        while messages and messages[0].get("role") != "user":
            messages = messages[1:]
        return [_strip_private(m) for m in messages]

    def _execute(self, session: Session, call: dict) -> tuple[str, Result]:
        function = call.get("function") or {}
        name = function.get("name") or ""
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError:
            return name, Result({"error": "arguments were not valid JSON"})
        if name == "load_plugin":
            wanted = arguments.get("name") if isinstance(arguments, dict) else None
            if wanted not in self.config["enabled"]:
                return name, Result({"error": "no such enabled plugin"})
            if wanted not in session.loaded:
                session.loaded.append(wanted)
            plugin = self.plugins[wanted]
            return name, Result({"loaded": wanted, "tools": [t.name for t in plugin.tools]})
        plugin_name, _, tool_name = name.partition(SEP)
        plugin = self.plugins.get(plugin_name)
        tool = next((t for t in plugin.tools if t.name == tool_name), None) if plugin else None
        if tool is None or plugin_name not in session.loaded or plugin_name not in self.config["enabled"]:
            return name, Result({"error": "unknown or unloaded tool"})
        try:
            params = tool.params.model_validate(arguments)
        except ValidationError as exc:
            problems = [{"field": ".".join(map(str, e["loc"])), "problem": e["msg"]} for e in exc.errors()]
            return name, Result({"error": "invalid arguments", "details": problems[:5]})
        try:
            return name, tool.handler(self.context(session, plugin_name), params)
        except ValueError as exc:
            return name, Result({"error": str(exc)})
        except Exception as exc:  # A plugin failure must not take the turn down.
            logger.warning("Tool %s failed: %s", name, type(exc).__name__, exc_info=True)
            return name, Result({"error": "the tool failed; try again later"})

    def _caption(self, session: Session, text: str) -> str:
        text = (text or "").strip()
        for plugin in self.enabled():
            if text and plugin.name in session.loaded and plugin.reply_guard:
                text = plugin.reply_guard(text, self.context(session, plugin.name))
        return text

    def _add_caption(self, session: Session, raw: str, blocks: list[dict]) -> None:
        raw = raw.strip()
        caption = self._caption(session, raw)
        if caption:
            blocks.append({"type": "text", "text": caption})
        elif raw:
            # Say so rather than go quiet: a plugin vetoed the prose.
            blocks.append({"type": "notice", "level": "info",
                           "text": "（模型的回复提到了无法核实的内容，已隐藏。）"})

    def run_turn(self, session: Session, text: str, attachments: list[str] = ()) -> list[dict]:
        notes = [f"[附件 {aid}: {session.attachments[aid]['name']}]" for aid in attachments
                 if aid in session.attachments]
        session.messages.append({"role": "user", "content": "\n".join([text, *notes]).strip()})
        blocks: list[dict] = []
        failures = 0
        for _ in range(MAX_STEPS):
            message = llm.chat(self.config["model"], [{"role": "system", "content": self._system_prompt(session)},
                                                      *self._history(session)], self._tool_specs(session))
            calls = [c for c in message.get("tool_calls") or [] if isinstance(c, dict) and c.get("id")]
            session.messages.append({"role": "assistant", "content": message.get("content") or "",
                                     **({"tool_calls": calls} if calls else {})})
            if not calls:
                self._add_caption(session, message.get("content") or "", blocks)
                return blocks
            all_final, all_failed = True, True
            for call in calls:
                name, result = self._execute(session, call)
                plugin_name = name.partition(SEP)[0]
                if name != "load_plugin":
                    blocks.append({"type": "activity", "plugin": plugin_name,
                                   "text": self.plugins[plugin_name].title if plugin_name in self.plugins else name,
                                   "tool": name.partition(SEP)[2], "error": isinstance(result.content, dict)
                                   and "error" in result.content})
                blocks.extend({"plugin": plugin_name, **block} for block in result.blocks)
                content = json.dumps(result.content, ensure_ascii=False, default=str)
                session.messages.append({"role": "tool", "tool_call_id": call["id"],
                                         "content": content[:TOOL_CONTENT_LIMIT]})
                all_final = all_final and result.final and name != "load_plugin"
                all_failed = all_failed and isinstance(result.content, dict) and "error" in result.content
            failures = failures + 1 if all_failed else 0
            if failures >= 2:
                # The model is retrying the same dead end; stop instead of burning steps.
                blocks.append({"type": "notice", "level": "warning",
                               "text": "工具连续调用失败，已停止。可以换个说法，或直接点选上面的结果。"})
                return blocks
            if all_final:
                # The tools are done; the model still talks to the user, once,
                # without tools so it cannot start another round.
                reply = llm.chat(self.config["model"],
                                 [{"role": "system", "content": self._system_prompt(session, follow_up=True)},
                                  *self._history(session)], [])
                session.messages.append({"role": "assistant", "content": reply.get("content") or ""})
                self._add_caption(session, reply.get("content") or "", blocks)
                return blocks
        blocks.append({"type": "notice", "level": "warning", "text": "这一轮步骤太多，已停止。请换个说法再试。"})
        return blocks

    def run_action(self, session: Session, plugin_name: str, action: str, payload: dict) -> list[dict]:
        """A button in a plugin's UI: deterministic, no model call."""
        plugin = self.plugins.get(plugin_name)
        handler = plugin.actions.get(action) if plugin else None
        if handler is None or plugin_name not in self.config["enabled"]:
            raise ValueError("这个操作不可用。")
        result = handler(self.context(session, plugin_name), payload if isinstance(payload, dict) else {})
        if result.content is not None:
            # The user acted through this plugin, so it is in play for the model
            # too. A pure view refresh (content None) loads nothing.
            if plugin_name not in session.loaded:
                session.loaded.append(plugin_name)
            # The model should know what the user did on screen, but this note
            # is not the user's words: grounding checks skip it (``_note``).
            session.messages.append({"role": "user", "_note": True, "content":
                                     f"[界面操作 {plugin_name}.{action}] "
                                     + json.dumps(result.content, ensure_ascii=False, default=str)[:2000]})
        return [{"plugin": plugin_name, **block} for block in result.blocks]
