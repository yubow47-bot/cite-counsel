"""The session record store: reference, not copy.

Everything a tool produces that can carry evidence -- a Record, an Artifact,
a Finding -- is stored here once and handed to the model only as a numbered
reference plus a short summary. Function tools take references as arguments
and the store resolves them to the original object before the plugin sees the
call, so the model cannot rewrite a value in flight.

Two gates live here:

- **Category vs. origins** (§3.2 of the blueprint): a data source may only
  produce ``database`` fields, an extraction plugin only ``extracted`` fields,
  a function plugin only Artifacts / Findings. The built-in ``record.*`` tools
  are the only writers of ``user`` fields, and only from the user's own words.
- **Leaf provenance**: every derivation chain of a saved Artifact or Finding
  is walked leaf by leaf and audited against the store's own evidence --
  what data sources and extraction plugins have actually put into *this*
  session, plus the parameter values the harness verified for this call.
  A ``database`` leaf that no source ever produced here is refused, so a
  function plugin cannot mint a fabricated value and have the artifact
  counted as "verified".

Records themselves are not a sandbox: plugins are trusted, installed code.
The checks are early, mechanical interception of category and provenance
misuse -- notably of anything that could fake the "verified" verdict.
"""

from __future__ import annotations

import threading
from typing import Any

from core.tool_contracts import Artifact, Field, Finding, Record, derivation_leaves

CATEGORY_ORIGIN = {"source": {"database"}, "extract": {"extracted"}}
PREFIX = {Record: "rec", Artifact: "art", Finding: "fnd"}
MAX_EVIDENCE = 20000


class ContractError(ValueError):
    """A tool returned something its plugin category or its own derivation
    cannot support."""


class Store:
    """Numbered objects for one session. Thread-safe; the session lock
    already serialises turns, but actions run under the same lock too."""

    def __init__(self):
        self._objects: dict[str, Any] = {}
        self._meta: dict[str, dict] = {}
        self._evidence: set[tuple[str, str, str | None]] = set()
        self._lock = threading.Lock()

    # ── Storage ───────────────────────────────────────────────────────

    def put(self, obj: Any, meta: dict | None = None) -> str:
        with self._lock:
            ref = f"{PREFIX[type(obj)]}_{len(self._objects) + 1}"
            self._objects[ref] = obj
            if meta:
                self._meta[ref] = meta
            self._register(obj)
            return ref

    def restore(self, entries: list[tuple[str, Any, dict | None]]) -> None:
        """Refill a revived session: exact refs, numbering continues after."""
        with self._lock:
            for ref, obj, meta in entries:
                self._objects[ref] = obj
                if meta:
                    self._meta[ref] = meta
                self._register(obj)

    def next_ref(self, obj: Any) -> str:
        return f"{PREFIX[type(obj)]}_{len(self._objects) + 1}"

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

    def entries(self) -> list[tuple[str, Any, dict | None]]:
        """Everything, for persistence."""
        with self._lock:
            return [(ref, obj, self._meta.get(ref)) for ref, obj in self._objects.items()]

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

    def check_output(self, category: str, results: list, allowed_user: frozenset[str] = frozenset()) -> None:
        """A tool's declared category vs. what it is trying to store.

        ``allowed_user`` are the values this call may legitimately label as
        user-origin: slices the harness verified against the user's words
        (``UserText`` parameters), plus the call's own parameter values.
        """
        for obj in results:
            self._check_one(category, obj, allowed_user)

    def _check_one(self, category: str, obj: Any, allowed_user: frozenset[str]) -> None:
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
            self._audit(obj.derivation, allowed_user)

    def _audit(self, derivation, allowed_user: frozenset[str]) -> None:
        """Every leaf must trace to evidence this session actually holds."""
        try:
            leaves = list(derivation_leaves(derivation))
        except ValueError as exc:
            raise ContractError(f"推导链无法审计：{exc}") from exc
        for leaf in leaves:
            if leaf.origin == "computed":
                if leaf.derivation is None:
                    raise ContractError("computed 字段必须带推导链。")
            elif leaf.origin == "database":
                if not leaf.source_id:
                    raise ContractError("database 叶子必须带来源标识。")
                if (leaf.origin, leaf.value, leaf.source_id) not in self._evidence:
                    raise ContractError(
                        "推导链里的 database 叶子在本次会话里没有出处"
                        f"（{_clip(leaf.value, 60)} / {leaf.source_id}）。")
            elif leaf.origin == "user":
                if leaf.value not in allowed_user and (leaf.origin, leaf.value, leaf.source_id) not in self._evidence:
                    raise ContractError(
                        f"user 叶子既不来自本调用已核验的用户参数，也不在会话证据里（{_clip(leaf.value, 60)}）。")
            elif leaf.origin == "extracted":
                if (leaf.origin, leaf.value, leaf.source_id) not in self._evidence:
                    raise ContractError(
                        f"extracted 叶子在本次会话里没有出处（{_clip(leaf.value, 60)}）。")

    def _register(self, obj: Any) -> None:
        """Record what evidence this session now verifiably holds."""
        if len(self._evidence) >= MAX_EVIDENCE:
            return
        if isinstance(obj, Record):
            for field in obj.fields.values():
                self._evidence.add((field.origin, field.value, field.source_id))
        elif isinstance(obj, (Artifact, Finding)):
            for leaf in derivation_leaves(obj.derivation):
                self._evidence.add((leaf.origin, leaf.value, leaf.source_id))

    # ── Built-in user-field and blank-record tools ────────────────────

    def add_user_field(self, record: Record, field: str, said: str) -> Record:
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
