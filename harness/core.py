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
comes from tool blocks. The model's prose is a caption, and the harness does
not veto it -- it stamps every fact in it with its source
(``harness.grounding``): matched facts carry the record they came from,
unmatched ones are reported as unsourced and still shown.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core import mcgill_format
from harness import grounding, llm
from harness.plugin import Plugin, Result, UserText, discover
from harness.records import ContractError, _probe
from harness.session import HISTORY_LIMIT, Context, Session, SessionStore
from core.tool_contracts import Artifact, Field as EvidenceField, Finding, Record
from pydantic import BaseModel, Field
from typing import Literal

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / ".chatbox-runtime" / "harness.json"
MAX_STEPS = 15  # tool rounds per turn; a round that only loads plugins is free
TOOL_CONTENT_LIMIT = 6000
SEP = "__"  # model-facing tool names: plugin__tool (dots are not allowed there)
FOLLOW_UP = (
    "\n\nThe tool results are now on the user's screen. If the user's request still needs another step "
    "(they asked for a citation and you only have the record, say), take it with the tools. Otherwise "
    "reply to the user in one to three sentences: what you found or did, and what they can do next (e.g. which candidate is most likely what they "
    "meant, and that they can pick it). You may mention facts that appear in the tool results; do not "
    "add any that do not. Render a citation with the cite tool when you can; you may also write one "
    "yourself from a record's fields, following the McGill rules -- say which record it came from.")


# Some models write their chain of thought as <think> tags inside ``content``
# instead of the structured ``reasoning`` field. Either way it becomes a
# collapsible thinking block and stays out of the reply text.
_THINK_PAIR = re.compile(r"<think>(.*?)</think>", re.S)
_THINK_OPEN = re.compile(r"<think>(.*)\Z", re.S)


def _split_thinking(content: str) -> tuple[str, str]:
    """(reply, thinking): every <think> section moves out of the content. An
    unclosed <think> (a turn cut off mid-thought) counts as thinking to the
    end of the text; sections join with blank lines."""
    if '<think>' not in content and '</think>' not in content:
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


class _ThinkSniffer:
    """The streaming counterpart to ``_split_thinking``: watches content
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
        """An unclosed ``<think>`` at the end of the stream counts as
        reasoning to the end, matching ``_split_thinking``."""
        if self._mode == "think" and self._buffer:
            text, self._buffer = self._buffer, ""
            return [text]
        if self._buffer:
            self.plain.append(self._buffer)
            self._buffer = ""
        return []


def _call_name(call: dict) -> str:
    return (call.get("function") or {}).get("name") or ""


def _strip_private(message: dict) -> dict:
    return {k: v for k, v in message.items() if not k.startswith("_")}


# Fixed-format inputs the harness may point out (§5.2). Only a hint: which
# plugin to use stays the model's decision.
_INPUT_HINTS = (
    (re.compile(r"\b10\.\d{4,9}/\S+"), "the message contains a DOI"),
    (re.compile(r"\b(?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dxX]\b"), "the message contains an ISBN"),
    (re.compile(r"\b\d{4}\s+[A-Z][A-Za-z]{1,5}\s+\d+\b"), "the message contains a neutral citation"),
    (re.compile(r"\[\d{4}\]"), "the message contains a bracketed citation year"),
    (re.compile(r"\b[CS]\s*-?\s*\d+\b", re.I), "the message may name a federal bill (e.g. C-22)"),
)


def _input_hints(text: str) -> str:
    if not text:
        return ""
    found = [note for pattern, note in _INPUT_HINTS if pattern.search(text)]
    if not found:
        return ""
    return "[输入提示: " + "; ".join(found) + ". These are only hints; decide yourself which plugin fits.]"


class _Builtin:
    """One harness-owned tool, available in every session without loading."""

    def __init__(self, name, description, params, handler):
        self.name, self.description, self.params, self.handler = name, description, params, handler


# ── Harness-owned record tools (§4.2 of the blueprint) ────────────────


class AddField(BaseModel):
    """Write one field, saying where it came from when you know.

    ``value`` is what to write; ``from_ref`` / ``from_field`` point at the
    record it was read from. The source is a label, never a gate: the harness
    looks for the value in the named record, then in every stored record,
    then in the user's words, and whatever it finds becomes the field's
    origin. A value found nowhere is still written, marked ``model`` --
    shown as unverified, never refused.
    """

    ref: str = Field(description="The record to extend, e.g. rec_3")
    field: str = Field(min_length=1, max_length=40, description="The field name, e.g. place or pinpoint")
    value: str | None = Field(default=None, min_length=1, max_length=500,
                              description="The value to write. Leave it out to copy from_field whole.")
    from_ref: str | None = Field(default=None, max_length=20,
                                 description="Where the value came from, e.g. rec_2 (a database, web or file record)")
    from_field: str | None = Field(default=None, max_length=40,
                                   description="The field on from_ref holding the value, e.g. date or text")


_RECORD_TYPES = tuple(sorted(mcgill_format.schemas().keys()))


class NewRecord(BaseModel):
    record_type: Literal[_RECORD_TYPES] = Field(
        description="The kind of source, matching what the citation plugin renders it as "
                    "(jurisprudence for a case, journal_article for an article, website for a webpage)")


_FIELD_STATUS = {
    "database": "已核验（字段可溯源到数据库）",
    "extracted": "未核验（来自文件提取）",
    "user": "未核验（用户补充）",
    "model": "未核验（模型填写，未经核实）",
}


def _from_named(ctx, p: AddField) -> tuple[EvidenceField, str] | None:
    """The value as found in the record the model named: the whole field when
    no value is given, or the value read out of that record's text."""
    if not p.from_ref:
        return None
    try:
        source = ctx.records.get(p.from_ref, Record)
    except Exception:
        return None
    fields = ([source.fields[p.from_field]] if p.from_field in source.fields
              else list(source.fields.values()))
    if p.value is None:
        if p.from_field in source.fields:
            return source.fields[p.from_field], p.from_ref
        return None
    probe = _probe(p.value)
    for found in fields:
        if found.origin != "model" and probe and probe in _probe(found.value):
            return EvidenceField(p.value, found.origin, source_id=found.source_id), p.from_ref
    return None


def _add_field(ctx, p: AddField) -> Result:
    record = ctx.records.get(p.ref, Record)
    named = _from_named(ctx, p)
    if named:
        field, from_ref = named
    elif p.value is not None:
        field, from_ref = ctx.provenance(p.value)
    else:
        raise ValueError(f"Nothing to write: pass value, or from_ref + from_field naming an existing field "
                         f"({p.from_ref or '?'} has no field {p.from_field or '?'}).")
    updated = ctx.records.add_field(record, p.field, field)
    ref = ctx.save(updated, supersedes=p.ref)
    source = {"database": "数据库字段", "extracted": "网页/文件提取", "user": "你的原话"}.get(field.origin, "模型填写")
    origin_note = f"{from_ref}（{source}）" if from_ref and field.origin != "user" else source
    note = ("the value is stored with its true origin; the citation is verified only while every field "
            "traces to a database. To add another field, use this ref -- not the one you passed in.")
    if field.origin == "model":
        note += (" This value was not found in any stored record or the user's messages, so it is marked "
                 "model-supplied (unverified). If you read it from a record, pass that record as from_ref.")
    return Result(
        {"ref": ref, "field": p.field, "value": field.value, "origin": field.origin, "from_ref": from_ref,
         "note": note},
        [{"type": "card", "title": f"已写入 {p.field}",
          "rows": [[p.field, field.value], ["来源", origin_note], ["核验状态", _FIELD_STATUS[field.origin]]],
          "note": "字段来源照实记录：来自某条记录的会附上编号；模型自己填的会明确标出，引用照常生成，"
                  "但含它的引文不会标记为已核验。"}])


def _new_record(ctx, p: NewRecord) -> Result:
    harness = ctx._harness
    web_available = "web" in harness.config["enabled"] or "web" in ctx.session.loaded
    if web_available and not ctx.session.web_search_used:
        # Giving up on the databases is allowed only after the web was tried;
        # a session that never searched would fabricate its way to a record.
        return Result(
            {"error": "数据库里没找到，但你还没有尝试网络搜索。先调用 web__search 找找看，"
                      "找不到再回来建空记录。"},
            [{"type": "notice", "level": "warning",
              "text": "还没尝试过网络搜索，暂不新建空记录。请先 web__search。"}])
    record = ctx.records.new_record(p.record_type)
    ref = ctx.records.put(record)
    field_names = [f["name"] for f in mcgill_format.schema_fields(p.record_type)]
    return Result(
        {"ref": ref, "record_type": record.source_type, "fields": {},
         "fields_this_type_takes": field_names,
         "note": "an empty record; write its fields with record__add_field -- copy each value from the "
                "records already on screen (it keeps its database origin) or from the user's words"},
        [{"type": "card", "title": f"新建空白记录 · {p.record_type}", "rows": [["编号", ref]],
          "note": "数据库里没有找到。这个记录可以直接由你填写（record__add_field）：优先从屏幕上已有的记录"
                  "复制字段值，其次是用户原话；每个值都会照实标记来源。需要用户补的，让用户直接在输入框里"
                  "写（" + "、".join(field_names) + "）。"}])


BUILTIN_TOOLS = (
    _Builtin("record__add_field",
             "Write a value into a stored record. Pass value, and when you read it from a record say which: "
             "from_ref (e.g. rec_2) and from_field (e.g. text for a fact inside a fetched page, date for the "
             "page's date). A value found in that record keeps its origin, so the citation can stay verified; "
             "omit value to copy from_field whole. A value found nowhere is still written, marked "
             "model-supplied and unverified -- prefer copying from a record or the user's words. Each call "
             "returns a NEW ref for the updated record -- to add a second field, call this again with THAT "
             "ref, not the one you started with. Do not call this twice in the same turn against the same "
             "record: the second call would not see the first one's field.",
             AddField, _add_field),
    _Builtin("record__new",
             "Create an empty record for a source the databases do not have, for you and the user to fill in. "
             "When the web plugin is enabled, it is refused until you have called web__search once this session.",
             NewRecord, _new_record),
)


class Harness:
    def __init__(self, plugins: dict[str, Plugin] | None = None, *, model: str = "",
                 config_path: Path = CONFIG_PATH):
        self.plugins = plugins if plugins is not None else discover()
        self.config_path = config_path
        self.sessions = SessionStore(store_dir=config_path.parent / "sessions")
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
            "Reply in the language of the user's latest message, in one to three short sentences.",
            "You act only through plugins. A plugin's tools become available after you call "
            "load_plugin; load only what this request needs.",
            "Results the tools show the user are authoritative. Do not restate or alter their facts "
            "in your reply, and never invent facts, sources, citations, dates or numbers. If no "
            "plugin can do what is asked, say so plainly.",
            "When you write a value into a record, name where it came from -- a record on screen "
            "(from_ref/from_field) or the user's own words. A value from neither is still written, but "
            "it is marked model-supplied and the citation reports itself unverified -- prefer a real "
            "source when one is on screen.",
            "When a lookup finds nothing, do not give up yet: search again in another form (the citation "
            "alone, the name alone), or load another plugin that could plausibly hold it. If the web plugin "
            "is enabled, you must call web__search at least once before record__new will open an empty "
            "record. After searching in vain: start the record, copy what you can from the records on "
            "screen, and ask the user for exactly the fields still missing.",
        ]
        latest = next((t for t in reversed(session.user_texts()) if t.strip()), "")
        # Chinese characters in the latest message are the one clear signal;
        # anything else replies in the language the user actually wrote in,
        # including a short one like "hello" -- no default language is baked in.
        language = ("Reply in Simplified Chinese." if any("一" <= ch <= "鿿" for ch in latest)
                    else "Reply in the language of the user's latest message.")
        if waiting:
            lines += ["", "Plugins you can load:"] + [f"- {p.name}: {p.description}" for p in waiting]
        disabled = [p for p in self.plugins.values() if p.name not in self.config["enabled"]]
        if disabled:
            lines += ["", "Installed but disabled (the user can enable them in the settings bar; "
                          "you cannot load these, and no data source here covers them):"] + [
                f"- {p.name}: {p.description}" for p in disabled]
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
        for spec in BUILTIN_TOOLS:
            specs.append({"type": "function", "function": {
                "name": spec.name, "description": spec.description,
                "parameters": spec.params.model_json_schema()}})
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
            builtin = next((b for b in BUILTIN_TOOLS if b.name == name), None)
            if builtin is not None:
                return self._run_builtin(session, builtin, arguments, name)
            return name, Result({"error": "unknown or unloaded tool"})
        ctx = self.context(session, plugin_name)
        try:
            params = self._prepare_params(ctx, tool.params.model_validate(arguments))
        except ValidationError as exc:
            problems = [{"field": ".".join(map(str, e["loc"])), "problem": e["msg"]} for e in exc.errors()]
            return name, Result({"error": "invalid arguments", "details": problems[:5]})
        except ValueError as exc:
            return name, Result({"error": str(exc)})
        try:
            # The plugin stores evidence itself (ctx.save) and answers with refs.
            result = tool.handler(ctx, params)
        except ContractError as exc:
            return name, Result({"error": "contract violation", "details": str(exc)})
        except ValueError as exc:
            return name, Result({"error": str(exc)})
        except Exception as exc:  # A plugin failure must not take the turn down.
            logger.warning("Tool %s failed: %s", name, type(exc).__name__, exc_info=True)
            return name, Result({"error": "the tool failed; try again later"})
        return name, result

    def _run_builtin(self, session: Session, builtin: _Builtin, arguments: Any, name: str) -> tuple[str, Result]:
        ctx = Context(session, "harness", self)
        try:
            params = self._prepare_params(ctx, builtin.params.model_validate(arguments))
            result = builtin.handler(ctx, params)
        except ValidationError as exc:
            problems = [{"field": ".".join(map(str, e["loc"])), "problem": e["msg"]} for e in exc.errors()]
            return name, Result({"error": "invalid arguments", "details": problems[:5]})
        except ValueError as exc:
            return name, Result({"error": str(exc)})
        except Exception as exc:
            logger.warning("Tool %s failed: %s", name, type(exc).__name__, exc_info=True)
            return name, Result({"error": "the tool failed; try again later"})
        return name, result

    def _prepare_params(self, ctx: Context, params: Any) -> Any:
        """Check the call before the plugin sees it (§3.4).

        ``UserText`` parameters that really are the user's words are replaced
        by the exact slice, and both the slice and every plain parameter value
        become the call's claimable user values for the provenance audit. A
        ``UserText`` value the user never wrote passes through untouched: the
        handler records its true origin (``Context.provenance``) instead of
        the harness refusing the call.
        """
        verified, plain = set(), set()

        def collect(value: Any) -> None:
            if isinstance(value, UserText):
                return
            if isinstance(value, (str, int, float, bool)):
                plain.add(str(value))
            elif isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, dict):
                for key, item in value.items():
                    plain.add(str(key))
                    collect(item)

        for name, value in params:
            collect(value)
            if isinstance(value, UserText):
                said = ctx.user_said(str(value))
                if said:
                    setattr(params, name, said)
                    verified.add(said)
                    plain.add(said)
        ctx.user_values, ctx.param_values = frozenset(verified), frozenset(plain)
        return params

    def _fact_index(self, session: Session) -> list[dict]:
        """What a fact in prose may be traced to, most linkable first: every
        stored record field, then every tool result, then the user's own
        words. The model's own prose is not here -- it could otherwise
        launder an invented fact into "already said"."""
        index = []
        for ref, record in session.records.all(Record):
            for name, field in record.fields.items():
                index.append({"kind": "record", "ref": ref, "field": name, "origin": field.origin,
                              "source_id": field.source_id, "text": field.value})
        for message in session.messages:
            role = message.get("role")
            if role == "tool":
                index.append({"kind": "tool", "text": message.get("content") or ""})
            elif role == "user" and not message.get("_note") and isinstance(message.get("content"), str):
                index.append({"kind": "user", "text": message["content"]})
        return index

    def _annotate_reply(self, session: Session, text: str) -> list[dict]:
        """Per-fact provenance for the model's prose (§5.4). Shapes from every
        enabled plugin apply, loaded or not; a fact found in the session's
        evidence carries its source, one found nowhere is reported unsourced.
        The prose itself is never removed."""
        shapes: list[re.Pattern] = []
        for plugin in self.enabled():
            shapes.extend(grounding.fact_shapes(plugin))
        return grounding.annotate_facts(text, self._fact_index(session), tuple(shapes))

    def _add_caption(self, session: Session, raw: str, blocks: list[dict]) -> None:
        raw = raw.strip()
        if not raw:
            return
        facts = self._annotate_reply(session, raw)
        blocks.append({"type": "text", "text": raw, **({"facts": facts} if facts else {})})

    def run_turn(self, session: Session, text: str, attachments: list[str] = ()) -> list[dict]:
        notes = [f"[附件 {aid}: {session.attachments[aid]['name']}]" for aid in attachments
                 if aid in session.attachments]
        hints = _input_hints(text)
        session.messages.append({"role": "user", "content": "\n".join([text, *notes]).strip()
                                 + (("\n\n" + hints) if hints else "")})
        try:
            return self._turn(session)
        finally:
            self.sessions.save(session)

    def _turn(self, session: Session) -> list[dict]:
        blocks: list[dict] = []
        failures = 0
        steps = 0
        follow_up = False
        # Loading is bounded by the plugin count, so free loads still end.
        for _ in range(MAX_STEPS + len(self.plugins)):
            if steps >= MAX_STEPS:
                break
            message = llm.chat(self.config["model"], [{"role": "system",
                                                       "content": self._system_prompt(session, follow_up)},
                                                      *self._history(session)], self._tool_specs(session))
            reasoning = message.get("reasoning")
            if not isinstance(reasoning, str) or not reasoning.strip():
                reply, reasoning = _split_thinking(message.get("content") or "")
                message = {**message, "content": reply}
            if isinstance(reasoning, str) and reasoning.strip():
                blocks.append({"type": "thinking", "text": reasoning.strip()})
            calls = [c for c in message.get("tool_calls") or [] if isinstance(c, dict) and c.get("id")]
            session.messages.append({"role": "assistant", "content": message.get("content") or "",
                                     **({"tool_calls": calls} if calls else {})})
            if not calls:
                self._add_caption(session, message.get("content") or "", blocks)
                return blocks
            if any(_call_name(c) != "load_plugin" for c in calls):
                steps += 1
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
            # A final result is ready to show, not the end of the request: the
            # model keeps its tools and either takes the next step (a citation
            # from the record it just got) or replies. The step budget bounds it.
            follow_up = all_final
        blocks.append({"type": "notice", "level": "warning", "text": "这一轮步骤太多，已停止。请换个说法再试。"})
        return blocks

    def run_turn_stream(self, session: Session, text: str, attachments: list[str] = ()):
        """Same as ``run_turn``, live: a generator of ``(kind, payload)``
        pairs -- ``("thinking_delta", text)`` as the model's reasoning
        streams in, then ``("block", block)`` for each block ``run_turn``
        would have returned, in the same order. The reply text itself still
        lands as one whole ``text`` block, same as before -- only the
        thinking is meant to be watched as it happens."""
        notes = [f"[附件 {aid}: {session.attachments[aid]['name']}]" for aid in attachments
                 if aid in session.attachments]
        hints = _input_hints(text)
        session.messages.append({"role": "user", "content": "\n".join([text, *notes]).strip()
                                 + (("\n\n" + hints) if hints else "")})
        try:
            yield from self._turn_events(session)
        finally:
            self.sessions.save(session)

    def _turn_events(self, session: Session):
        failures = 0
        steps = 0
        follow_up = False
        # Loading is bounded by the plugin count, so free loads still end.
        for _ in range(MAX_STEPS + len(self.plugins)):
            if steps >= MAX_STEPS:
                break
            history = [{"role": "system", "content": self._system_prompt(session, follow_up)}, *self._history(session)]
            tools = self._tool_specs(session)
            sniffer = _ThinkSniffer()
            reasoning_parts: list[str] = []
            sniffed_parts: list[str] = []
            message: dict = {}
            for kind, payload in llm.chat_stream(self.config["model"], history, tools):
                if kind == "reasoning" and payload:
                    reasoning_parts.append(payload)
                    yield "thinking_delta", payload
                elif kind == "content" and payload:
                    for text in sniffer.feed(payload):
                        if text:
                            sniffed_parts.append(text)
                            yield "thinking_delta", text
                elif kind == "done":
                    message = payload
            for text in sniffer.flush():
                if text:
                    sniffed_parts.append(text)
                    yield "thinking_delta", text
            # Unlike _turn, no consolidated "thinking" block follows: the
            # thinking_delta events above already carried it, live -- adding
            # it again here would just repeat what the page already showed.
            # sniffer.plain has the <think> block peeled off, but not the leaked
            # <tool_call> markup chat_stream already recovered -- strip that too.
            plain = "".join(sniffer.plain)
            if "<tool_call>" in plain:
                plain = llm._LEAKED_CALL.sub("", plain).strip()
            message = {**message, "content": plain}
            calls = [c for c in message.get("tool_calls") or [] if isinstance(c, dict) and c.get("id")]
            session.messages.append({"role": "assistant", "content": message.get("content") or "",
                                     **({"tool_calls": calls} if calls else {})})
            if not calls:
                blocks: list[dict] = []
                self._add_caption(session, message.get("content") or "", blocks)
                for block in blocks:
                    yield "block", block
                return
            if any(_call_name(c) != "load_plugin" for c in calls):
                steps += 1
            all_final, all_failed = True, True
            for call in calls:
                name, result = self._execute(session, call)
                plugin_name = name.partition(SEP)[0]
                if name != "load_plugin":
                    yield "block", {"type": "activity", "plugin": plugin_name,
                                    "text": self.plugins[plugin_name].title if plugin_name in self.plugins else name,
                                    "tool": name.partition(SEP)[2], "error": isinstance(result.content, dict)
                                    and "error" in result.content}
                for block in result.blocks:
                    yield "block", {"plugin": plugin_name, **block}
                content = json.dumps(result.content, ensure_ascii=False, default=str)
                session.messages.append({"role": "tool", "tool_call_id": call["id"],
                                         "content": content[:TOOL_CONTENT_LIMIT]})
                all_final = all_final and result.final and name != "load_plugin"
                all_failed = all_failed and isinstance(result.content, dict) and "error" in result.content
            failures = failures + 1 if all_failed else 0
            if failures >= 2:
                yield "block", {"type": "notice", "level": "warning",
                                "text": "工具连续调用失败，已停止。可以换个说法，或直接点选上面的结果。"}
                return
            follow_up = all_final
        yield "block", {"type": "notice", "level": "warning", "text": "这一轮步骤太多，已停止。请换个说法再试。"}

    def run_action(self, session: Session, plugin_name: str, action: str, payload: dict) -> list[dict]:
        """A button in a plugin's UI: deterministic, no model call."""
        plugin = self.plugins.get(plugin_name)
        handler = plugin.actions.get(action) if plugin else None
        if handler is None or plugin_name not in self.config["enabled"]:
            raise ValueError("这个操作不可用。")
        try:
            result = handler(self.context(session, plugin_name), payload if isinstance(payload, dict) else {})
        finally:
            self.sessions.save(session)
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
