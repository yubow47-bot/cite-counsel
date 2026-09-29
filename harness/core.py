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
from core.tool_contracts import Field as EvidenceField, Record
from pydantic import BaseModel, Field, model_validator
from typing import Literal

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / ".chatbox-runtime" / "harness.json"
MAX_STEPS = 20  # tool rounds per turn; a round that only loads plugins is free
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


def _plain_schema(schema: dict) -> dict:
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


# ── Harness-owned record tool (§4.2 of the blueprint) ─────────────────


_RECORD_TYPES = tuple(sorted(mcgill_format.schemas().keys()))
# Types whose citation stands for a decision or an enactment itself; built
# only from news or web text, such a record cites a report, not the source.
_AUTHORITY_TYPES = {"jurisprudence", "legislation", "bill", "treaty", "foreign", "by_law"}


class ComposeField(BaseModel):
    name: str = Field(min_length=1, max_length=40, description="The field name, e.g. author, title, year")
    value: str = Field(min_length=1, max_length=500, description="What goes in the field")
    source: str | None = Field(default=None, max_length=20,
                               description="Where you read it: a record ref (rec_N), or \"user\" for the user's "
                                           "own words. Leave out when it has no source.")
    quote: str | None = Field(default=None, max_length=2000,
                              description="The exact words in that source that contain the value, copied. "
                                          "Leave out when the value is the whole source field.")


class Compose(BaseModel):
    record_type: Literal[_RECORD_TYPES] | None = Field(
        default=None, description="The kind of source, as the citation plugin renders it (jurisprudence for a "
                                  "case, journal_article for an article, website for a web page)")
    base_ref: str | None = Field(default=None, max_length=20,
                                 description="An existing record to add fields to or correct; its other fields "
                                             "are kept")
    fields: list[ComposeField] = Field(min_length=1, description="Every field to write, in this one call")

    @model_validator(mode="before")
    @classmethod
    def _fields_by_name(cls, data):
        # Seen live: a model sent a list of fields with no names and the
        # whole call was rejected. The list with an explicit name is the
        # shape; an object keyed by field name is accepted too.
        if isinstance(data, dict) and isinstance(data.get("fields"), dict):
            data = {**data, "fields": [{"name": name, **(item if isinstance(item, dict) else {"value": item})}
                                       for name, item in data["fields"].items()]}
        return data


_FIELD_STATUS = {
    "database": "Verified (traces to the database)",
    "extracted": "Unverified (extracted from a page or file)",
    "user": "Unverified (added by you)",
    "model": "Unverified (written by the model, not checked)",
}


def _evidence(ctx, value: str, source: str | None, quote: str | None) -> tuple[EvidenceField, str, str]:
    """(field, source ref, why unverified). A field inherits a source's origin
    only when the model names the source and quotes it: the quote must be in
    that source and the value inside the quote. No guessing across records."""
    if not source:
        return EvidenceField(value, "model"), "", "no source was given"
    evidence = quote or value
    if _probe(value) not in _probe(evidence):
        return EvidenceField(value, "model"), "", "the value is not inside the quote"
    if source.strip().lower() == "user":
        if ctx.user_said(evidence):
            return EvidenceField(value, "user"), "", ""
        return EvidenceField(value, "model"), "", "those words are not in the user's messages"
    try:
        record = ctx.records.get(source, Record)
    except ValueError:
        return EvidenceField(value, "model"), "", f"{source} is not a record in this conversation"
    probe = _probe(evidence)
    for found in record.fields.values():
        if found.origin != "model" and probe in _probe(found.value):
            return EvidenceField(value, found.origin, source_id=found.source_id), source, ""
    return EvidenceField(value, "model"), "", f"the quote is not in {source}"


def _stored_record(ctx, ref: str) -> bool:
    try:
        ctx.records.get(ref, Record)
    except ValueError:
        return False
    return True


def _compose(ctx, p: Compose) -> Result:
    if not p.fields:
        raise ValueError("Give at least one field.")
    base = ctx.records.get(p.base_ref, Record) if p.base_ref else None
    if base is None:
        # Enabled now, not loaded once: a plugin the user has since switched
        # off cannot run web__search, and the gate would never open.
        web_available = "web" in ctx._harness.config["enabled"]
        # A field quoting a stored record (an uploaded file, a fetched page, a
        # database hit) means the source is already in hand -- there is
        # nothing for a web search to find first.
        from_stored = any(item.source and _stored_record(ctx, item.source) for item in p.fields)
        if web_available and not from_stored and not ctx.session.web_search_used:
            # A new record is for a source the databases lack; the web is tried first.
            return Result(
                {"error": "Not found in the databases, and you have not tried a web search yet. Call "
                          "web__search first; compose a record only if that finds nothing citable."},
                [{"type": "notice", "level": "warning",
                  "text": "No web search has been tried yet, so no record was composed. Try web__search first."}])
    record_type = p.record_type or (base.source_type if base else None)
    if not record_type:
        raise ValueError("Give record_type, or base_ref to extend an existing record.")
    if base is not None and p.record_type and p.record_type != base.source_type:
        raise ValueError(f"{p.base_ref} is a {base.source_type} record, not {p.record_type}.")
    fields = dict(base.fields) if base else {}
    written, rejected = {}, {}
    for item in p.fields:
        name = item.name
        problem = mcgill_format.field_problem(record_type, name, item.value)
        if problem:
            rejected[name] = problem
            continue
        value = re.sub(r"\s+", " ", item.value).strip()
        field, from_ref, why = _evidence(ctx, value, item.source, item.quote)
        fields[name] = field
        written[name] = {"value": value, "origin": field.origin, "from_ref": from_ref,
                         **({"unverified_because": why} if why else {})}
    if not written:
        return Result({"error": "nothing was written: every field was rejected", "rejected": rejected},
                      [{"type": "notice", "level": "warning",
                        "text": "No record was written: " + "; ".join(f"{k}: {v}" for k, v in rejected.items())}])
    record = Record(record_type, fields, base.provider if base else "user", base.record_id if base else "blank")
    ref = ctx.save(record, supersedes=p.base_ref) if base else ctx.save(record)
    missing = mcgill_format.missing_summary(record_type, {n: f.value for n, f in fields.items()})
    warnings = []
    if record_type in _AUTHORITY_TYPES and not any(f.origin in ("database", "user") for f in fields.values()):
        sources = sorted({w["from_ref"] for w in written.values() if w["from_ref"]})
        if not sources:
            # Nothing was quoted: point at the pages and files this session holds.
            sources = [r for r, obj in ctx.records.all(Record)
                       if obj.source_type in ("website", "news_online", "document")][-3:]
        warnings.append(
            f"No field of this {record_type} record comes from a database or the user. A citation built from "
            f"news or web text is not a citation of the {record_type} itself: if the only source is a report "
            f"or a page, cite that record directly" + (f" ({', '.join(sources)})" if sources else "")
            + " and tell the user no reported source was found.")
    rows = []
    for name, entry in written.items():
        label = _FIELD_STATUS[entry["origin"]] + (f" — {entry['from_ref']}" if entry["from_ref"] else "")
        rows.append([name, f"{entry['value']}  ·  {label}"])
    rows += [["✕ " + name, reason] for name, reason in rejected.items()]
    if missing:
        rows.append(["Still missing", missing])
    return Result(
        {"ref": ref, "record_type": record_type, "written": written, "rejected": rejected, "missing": missing,
         "warnings": warnings,
         "note": "cite this ref with the citation plugin; rejected fields were not written -- fix their shape "
                 "or leave them out. A field is verified only when it traces to a database."},
        [{"type": "card", "title": f"Composed record · {record_type} · {ref}", "rows": rows,
          "note": " ".join(warnings) or "Each field shows where it came from. Citations still render; one with "
                                       "a non-database field is not marked verified."}])


BUILTIN_TOOLS = (
    _Builtin("record__compose",
             "Build a citation record in one call, for a source no stored record already covers. First: if a "
             "stored record already IS the source (a fetched page, an extracted file, a database hit), cite that "
             "record directly -- do not rebuild it. Pass record_type and every field at once, each as "
             "{name, value, source, quote} in a list: source is the record you read the value from (rec_N) or \"user\" for the "
             "user's own words; quote is the exact words in that source containing the value. A field whose "
             "quote checks out keeps that source's origin; one without a source is written as model-supplied, "
             "unverified. A value in the wrong shape for its field (a sentence where a citation number goes, a "
             "year that is not four digits) is not written and the result says why -- fix it or leave it out. "
             "Never write a citation number (neutral_citation, reporter) you do not actually have. Pass base_ref "
             "to add or correct fields on an existing record; the new version replaces it. When the web plugin "
             "is enabled, a new record that quotes no stored record is refused until web__search has run once "
             "this session.",
             Compose, _compose),
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
            raise ValueError("No such plugin.")
        with self._config_lock:
            current = [n for n in self.config["enabled"] if n != name]
            if enabled:
                current.append(name)
            else:
                dependents = [n for n in current if name in self.plugins[n].requires]
                if dependents:
                    raise ValueError("Disable the plugins that depend on it first: "
                                     + ", ".join(self.plugins[n].title for n in dependents))
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
            raise ValueError("No such plugin.")
        known = {s.name: s for s in plugin.settings}
        with self._config_lock:
            saved = dict(self.config["plugin_settings"].get(name) or {})
            for key, value in values.items():
                setting = known.get(key)
                if setting is None:
                    raise ValueError(f"This plugin has no setting named {key}.")
                if setting.kind == "bool":
                    value = bool(value)
                elif setting.kind == "choice":
                    if value not in setting.choices:
                        raise ValueError(f"{setting.label} has an invalid value.")
                elif not isinstance(value, str) or len(value) > 500:
                    raise ValueError(f"{setting.label} must be text of 500 characters or fewer.")
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
            "Cite what you actually found. A stored record -- a database hit, a fetched page, an extracted "
            "file -- is citable as it is: cite it directly. Build a record with record__compose only when "
            "no stored record is the source, and then quote, for each field, the words you read it from. "
            "An extracted file often already states its own citation (a cover page's \"Citation:\" line, "
            "the title and author on its first page): build the record from that text directly.",
            "A lookup elsewhere is a check, not the answer: compare what it returns -- author, title, year -- "
            "with what you are verifying. A result that does not match is a different work: do not cite it; "
            "say it did not match and use what you already have.",
            "When a lookup finds nothing, do not give up yet: search again in another form (the citation "
            "alone, the name alone), or load another plugin that could plausibly hold it. If what you find "
            "is only a report about a source (a news story about a decision), say that the source itself "
            "was not found and offer to cite the report -- do not present the report as the decision.",
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
                specs.append({"type": "function", "function": {
                    "name": plugin.name + SEP + tool.name, "description": tool.description,
                    "parameters": _plain_schema(tool.params.model_json_schema())}})
        for spec in BUILTIN_TOOLS:
            specs.append({"type": "function", "function": {
                "name": spec.name, "description": spec.description,
                "parameters": _plain_schema(spec.params.model_json_schema())}})
        return specs

    def _history(self, session: Session) -> list[dict]:
        """The model-visible slice: whole turns only, most recent last.

        A turn starts at its own user message and is never cut mid-way, no
        matter how many tool rounds it took. The old version sliced the
        last HISTORY_LIMIT messages first and only then trimmed forward to
        the next user message -- so a turn with more tool rounds than
        HISTORY_LIMIT (a long search-then-compose-then-cite
        chain) produced a window with no user message in it at all, and
        every message in it got trimmed away. The next call then got the
        system prompt alone, looking like a brand new conversation, with
        the turn actually in progress simply gone. HISTORY_LIMIT is now a
        soft budget: earlier whole turns are added while there is room,
        oldest first to drop, but the current turn is always kept whole.

        The current turn starts at the person's own message, not at the
        harness notes that follow it (an input hint, an attachment list):
        anchored on a note, a long turn dropped the request it was serving.
        """
        user_indices = [i for i, m in enumerate(session.messages) if m.get("role") == "user"]
        typed = [i for i in user_indices if not session.messages[i].get("_note")]
        if not user_indices:
            return []
        start = typed[-1] if typed else user_indices[-1]
        user_indices = [i for i in user_indices if i < start] + [start]
        for idx in reversed(user_indices[:-1]):
            if len(session.messages) - idx > HISTORY_LIMIT:
                break
            start = idx
        return [_strip_private(m) for m in session.messages[start:]]

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
        self._open_turn(session, text, attachments)
        try:
            return self._turn(session)
        finally:
            self.sessions.save(session)

    def _open_turn(self, session: Session, text: str, attachments: list[str]) -> None:
        """The user's words, then the harness's own notes about them. The
        input hint is the harness talking, not the user: kept inside the
        user's message, its Chinese label made an English question with a
        citation in it read as Chinese to the reply-language check."""
        session.messages.append({"role": "user", "content": text.strip()})
        hints = _input_hints(text)
        if hints:
            session.messages.append({"role": "user", "_note": True, "content": hints})
        self._note_attachments(session, attachments)

    def _note_attachments(self, session: Session, attachments: list[str]) -> None:
        # A filename is not the user's words -- an uploaded file's own name
        # ("...pp.161-189.pdf") must not be able to pass a fact it contains
        # (a page range, a year) off as something the user typed. Kept as
        # its own _note message: visible to the model, invisible to
        # user_said()'s grounding check.
        notes = [f"[附件 {aid}: {session.attachments[aid]['name']}]" for aid in attachments
                 if aid in session.attachments]
        if notes:
            session.messages.append({"role": "user", "_note": True, "content": "\n".join(notes)})

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
                               "text": "The tool failed twice in a row, so this stopped. Try rephrasing, or use the results above directly."})
                return blocks
            # A final result is ready to show, not the end of the request: the
            # model keeps its tools and either takes the next step (a citation
            # from the record it just got) or replies. The step budget bounds it.
            follow_up = all_final
        blocks.append({"type": "notice", "level": "warning", "text": "Too many steps this round, so this stopped. Try rephrasing."})
        return blocks

    def run_turn_stream(self, session: Session, text: str, attachments: list[str] = ()):
        """Same as ``run_turn``, live: a generator of ``(kind, payload)``
        pairs -- ``("thinking_delta", text)`` as the model's reasoning
        streams in, then ``("block", block)`` for each block ``run_turn``
        would have returned, in the same order. The reply text itself still
        lands as one whole ``text`` block, same as before -- only the
        thinking is meant to be watched as it happens."""
        self._open_turn(session, text, attachments)
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
                                "text": "The tool failed twice in a row, so this stopped. Try rephrasing, or use the results above directly."}
                return
            follow_up = all_final
        yield "block", {"type": "notice", "level": "warning", "text": "Too many steps this round, so this stopped. Try rephrasing."}

    def run_action(self, session: Session, plugin_name: str, action: str, payload: dict) -> list[dict]:
        """A button in a plugin's UI: deterministic, no model call."""
        plugin = self.plugins.get(plugin_name)
        handler = plugin.actions.get(action) if plugin else None
        if handler is None or plugin_name not in self.config["enabled"]:
            raise ValueError("This action is not available.")
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
