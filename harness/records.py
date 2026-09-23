"""The session record store: reference, not copy.

Everything a tool produces that can carry evidence -- a Record, an Artifact,
a Finding -- is stored here once and handed to the model only as a numbered
reference plus a short summary. Function tools take references as arguments
and the store resolves them to the original object before the plugin sees the
call, so the model cannot rewrite a value in flight.

The store also enforces the contract between plugin categories and origins
(§3.2 of the blueprint): a data source may only produce ``database`` fields,
an extraction plugin only ``extracted`` fields, a function plugin only
``computed`` derivations or verbatim re-use of input fields. The built-in
``record.*`` tools are the only writers of ``user`` fields, and only from
the user's own words.

Records themselves are not a sandbox: plugins are trusted, installed code.
The check is an early, mechanical interception of category misuse.
"""

from __future__ import annotations

import threading
from typing import Any

from core.tool_contracts import Artifact, Field, Finding, Record

CATEGORY_ORIGIN = {"source": {"database"}, "extract": {"extracted"}}
OBJECT_KINDS = {"Record": Record, "Artifact": Artifact, "Finding": Finding}
PREFIX = {Record: "rec", Artifact: "art", Finding: "fnd"}
VERDICTS = {"confirmed", "contradicted", "inconclusive"}


class ContractError(ValueError):
    """A tool returned something its plugin category may not produce."""


class Store:
    """Numbered objects for one session. Thread-safe; the session lock
    already serialises turns, but actions run under the same lock too."""

    def __init__(self):
        self._objects: dict[str, Any] = {}
        self._meta: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ── Storage ───────────────────────────────────────────────────────

    def put(self, obj: Any, meta: dict | None = None) -> str:
        with self._lock:
            ref = f"{PREFIX[type(obj)]}_{len(self._objects) + 1}"
            self._objects[ref] = obj
            if meta:
                self._meta[ref] = meta
            return ref

    def get(self, ref: str, kind: type | tuple[type, ...] | None = None) -> Any:
        obj = self._objects.get(str(ref or "").strip())
        if obj is None:
            raise ValueError("没有这个编号，或它不属于本次对话。")
        if kind is not None and not isinstance(obj, kind):
            raise ValueError(f"编号 {ref} 不是这个工具需要的对象。")
        return obj

    def meta(self, ref: str) -> dict:
        """The plugin-supplied sidecar of one stored object (e.g. the fields
        a citation was rendered from). Server-owned: refs are the only handle
        the model ever gets, so there is nothing to sign."""
        return self._meta.get(str(ref or "").strip(), {})

    def all(self, kind: type | tuple[type, ...] | None = None) -> list[tuple[str, Any]]:
        return [(ref, obj) for ref, obj in self._objects.items()
                if kind is None or isinstance(obj, kind)]

    def summary(self, ref: str) -> dict:
        """The model-facing view of one stored object."""
        obj = self._objects[ref]
        if isinstance(obj, Record):
            fields = {name: _clip(f.value) for name, f in obj.fields.items()}
            return {"ref": ref, "kind": "record", "record_type": obj.source_type,
                    "provider": obj.provider, "record_id": obj.record_id, "fields": fields}
        if isinstance(obj, Artifact):
            return {"ref": ref, "kind": "artifact", "artifact_kind": obj.kind,
                    "content": obj.content if isinstance(obj.content, str) else "<binary>"}
        return {"ref": ref, "kind": "finding", "verdict": obj.verdict, "detail": obj.detail,
                "coverage": obj.coverage}

    # ── Contract checks ───────────────────────────────────────────────

    def check_output(self, category: str, result: Any) -> Any:
        """A tool's declared category vs. the origins inside its return value.

        Returns the object (or list of objects) to store. Mixed returns are
        not accepted: a tool call produces one stored object, or plain data.
        """
        if result is None or isinstance(result, (str, int, float, bool, dict)):
            return result
        for obj in result if isinstance(result, list) else [result]:
            self._check_one(category, obj)
        return result

    def _check_one(self, category: str, obj: Any) -> None:
        if isinstance(obj, Record):
            allowed = CATEGORY_ORIGIN.get(category)
            if allowed is None:
                raise ContractError(f"插件类别 {category} 不能产出记录。")
            for field in obj.fields.values():
                if field.origin not in allowed:
                    raise ContractError(
                        f"{category} 插件只能产出 {'/'.join(sorted(allowed))} 字段，"
                        f"不能产出 {field.origin}。")
        elif isinstance(obj, (Artifact, Finding)):
            if category != "function":
                raise ContractError("只有功能插件可以产出成品或结论。")

    # ── Built-in user-field and blank-record tools ────────────────────

    def add_user_field(self, record: Record, field: str, text: str, said: str) -> Record:
        """A new record version with one field written from the user's words.

        ``said`` is the verified slice of the user's message (``""`` when the
        model passed something the user never wrote -- callers must treat
        that as a refusal, not fall back to the model's text).
        """
        if not said.strip():
            raise ValueError("补充内容必须出自你自己的话，请直接在输入框里写。")
        name = str(field).strip()
        if not name:
            raise ValueError("需要字段名。")
        fields = {**record.fields, name: Field(said, "user")}
        return Record(record.source_type, fields, record.provider, record.record_id)

    def new_record(self, source_type: str) -> Record:
        return Record(str(source_type).strip() or "unknown", {}, "user", "blank")


def _clip(value: str, limit: int = 300) -> str:
    """Summaries are for orientation; long values (a full judgment text) stay out."""
    return value if len(value) <= limit else value[:limit] + "…"


def field_value(record: Record, name: str) -> str:
    field = record.fields.get(name)
    return field.value if field else ""
