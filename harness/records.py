"""The session record store: reference, not copy.

Everything a tool produces that can carry evidence -- a Record, an Artifact,
a Finding -- is stored here once and handed to the model only as a numbered
reference plus a short summary. Function tools take references as arguments
and the store resolves them to the original object before the plugin sees the
call, so the model cannot rewrite a value in flight.

Two gates live here:

- **Category vs. origins** (§3.2 of the blueprint): a data source may only
  produce ``database`` fields, an extraction plugin only ``extracted`` fields,
  a function plugin only Artifacts / Findings. The built-in
  ``record__compose`` is the only writer of ``user`` fields: a field inherits
  an origin only when the model names its source and quotes it, and the
  quote checks out; anything else is written as ``model``, never refused.
- **Leaf provenance**: every derivation chain of a saved Artifact or Finding
  is walked leaf by leaf and audited against the store's own evidence --
  what data sources and extraction plugins have actually put into *this*
  session, plus the parameter values the harness verified for this call.
  A ``database`` leaf that no source ever produced here is refused, so a
  function plugin cannot mint a fabricated value and have the artifact
  counted as "verified". A ``model`` leaf is never refused: it is stamped
  honestly (the model supplied it), and ``grounding_issues`` counts the
  artifact as unverified -- shown, not trusted.

Records themselves are not a sandbox: plugins are trusted, installed code.
The checks are early, mechanical interception of category and provenance
misuse -- notably of anything that could fake the "verified" verdict.
"""

from __future__ import annotations

import re
import threading
from dataclasses import replace
from typing import Any

from core.tool_contracts import Artifact, Derivation, Field, Finding, Record, derivation_leaves
from core.evidence_text import find_text

CATEGORY_ORIGIN = {"source": {"database"}, "extract": {"extracted"}}
PREFIX = {Record: "rec", Artifact: "art", Finding: "fnd"}
MAX_EVIDENCE = 20000
# The origins a copied value may legitimately inherit from stored evidence.
# ``model`` is deliberately absent: a value the model supplied is never
# evidence, so a copy of a copy cannot launder itself into a source.
BORROWED = {"database": 0, "extracted": 1, "user": 2}


def _probe(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


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
        # (origin, source_id) -> whitespace-normalized stored values: lets a
        # leaf count as sourced when its value appears inside a long stored
        # text from the same source (a fact read out of a fetched page, say).
        self._contained: dict[tuple[str, str], list[str]] = {}
        self._superseded_by: dict[str, str] = {}
        self._lock = threading.Lock()

    # ── Storage ───────────────────────────────────────────────────────

    def put(self, obj: Any, meta: dict | None = None, *, supersedes: str | None = None) -> str:
        with self._lock:
            ref = f"{PREFIX[type(obj)]}_{len(self._objects) + 1}"
            self._objects[ref] = obj
            if meta:
                self._meta[ref] = meta
            if supersedes:
                # A model that still cites the ref it started from (a batch of
                # calls issued before any of them had a result to chain from,
                # or just a stale reference in a later turn) reaches this
                # record's latest version instead of a dead end.
                old = str(supersedes).strip()
                if old and old != ref:
                    self._superseded_by[old] = ref
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

    def _current_ref(self, ref: str) -> str:
        seen = set()
        while ref in self._superseded_by and ref not in seen:
            seen.add(ref)
            ref = self._superseded_by[ref]
        return ref

    def get(self, ref: str, kind: type | tuple[type, ...] | None = None) -> Any:
        key = self._current_ref(str(ref or "").strip())
        obj = self._objects.get(key)
        if obj is None:
            raise ValueError("No such ref, or it does not belong to this conversation.")
        if kind is not None and not isinstance(obj, kind):
            raise ValueError(f"{ref} is not the kind of object this tool needs.")
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

    def check_output(self, category: str, results: list, allowed_user: frozenset[str] = frozenset()) -> list:
        """A tool's declared category vs. what it is trying to store.

        ``allowed_user`` are the values this call may legitimately label as
        user-origin: slices the harness verified against the user's words
        (``UserText`` parameters), plus the call's own parameter values.
        """
        return [self._check_one(category, obj, allowed_user) for obj in results]

    def _check_one(self, category: str, obj: Any, allowed_user: frozenset[str]) -> Any:
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
            return replace(obj, derivation=self._audit(obj.derivation, allowed_user))
        return obj

    def _sourced(self, origin: str, value: str, source_id: str | None) -> bool:
        """Database claims need a complete recorded value. Extracted claims
        may cite a bounded passage, without acquiring database verification."""
        if (origin, value, source_id) in self._evidence:
            return True
        if origin == "database":
            return False
        probe = _probe(value)
        if not probe:
            return False
        candidates = list(self._contained.get((origin, source_id), ()))
        if origin == "extracted":
            candidates += self._contained.get(("database", source_id), ())
        return any(find_text(candidate, probe) >= 0 for candidate in candidates)

    def _audit(self, derivation, allowed_user: frozenset[str]) -> Derivation:
        """Every leaf must trace to evidence this session actually holds.

        A leaf that claims an origin the session cannot back (a ``database``
        value no source ever produced here, say) is not refused: it is
        rewritten as ``model``, so the artifact is still produced and
        ``grounding_issues`` counts it as unverified. What cannot happen is a
        forged leaf keeping its ``verified`` standing. Only a malformed
        derivation (a ``computed`` leaf with no chain, an unauditable graph)
        is an error. Returns the derivation to store."""
        try:
            list(derivation_leaves(derivation))
        except ValueError as exc:
            raise ContractError(f"推导链无法审计：{exc}") from exc
        rebuilt: dict[int, Derivation] = {}

        def leaf(node: Field) -> Field:
            if node.origin == "computed":
                if node.derivation is None:
                    raise ContractError("computed 字段必须带推导链。")
                return replace(node, derivation=walk(node.derivation))
            if node.origin == "model":
                return node
            if node.origin in ("database", "extracted"):
                backed = (node.origin != "database" or bool(node.source_id)) and                     self._sourced(node.origin, node.value, node.source_id)
            else:  # user
                backed = node.value in allowed_user or (node.origin, node.value, node.source_id) in self._evidence
            return node if backed else Field(node.value, "model")

        def walk(node: Derivation) -> Derivation:
            if id(node) in rebuilt:  # a shared subtree, or a cycle: reuse what is being built
                return rebuilt[id(node)]
            rebuilt[id(node)] = node
            inputs = tuple(walk(i) if isinstance(i, Derivation) else leaf(i) for i in node.inputs)
            result = Derivation(inputs, node.rule_id, node.input_names)
            rebuilt[id(node)] = result
            return result

        return walk(derivation)

    def _register(self, obj: Any) -> None:
        """Record what evidence this session now verifiably holds. A ``model``
        leaf is a confession, not evidence -- registering it would let the
        next copy of the value resolve to a source it never had."""
        if len(self._evidence) >= MAX_EVIDENCE:
            return
        if isinstance(obj, Record):
            for field in obj.fields.values():
                if field.origin != "model":
                    self._evidence.add((field.origin, field.value, field.source_id))
                    if field.source_id and field.origin in BORROWED and field.origin != "user":
                        self._contained.setdefault((field.origin, field.source_id), []).append(_probe(field.value))
        elif isinstance(obj, (Artifact, Finding)):
            for leaf in derivation_leaves(obj.derivation):
                if leaf.origin != "model":
                    self._evidence.add((leaf.origin, leaf.value, leaf.source_id))

    # ── Built-in user-field and blank-record tools ────────────────────

    def resolve(self, value: str) -> tuple[Field, str]:
        """Where a value anyone (model or user) supplied already exists here.

        Exact record-field matches first -- database before extracted before
        user -- then a bounded passage inside a record's longer field. A
        passage in database text is extracted, with its source id preserved;
        finding text does not verify its role as a citation field. Model
        values are never evidence, so a copy of a copy cannot launder itself
        into a source.
        """
        probe = _probe(value)
        best = None
        for exact in (True, False):
            for ref, record in self.all(Record):
                for field in record.fields.values():
                    candidate = _probe(field.value)
                    hit = candidate == probe if exact else find_text(candidate, probe) >= 0
                    if not hit:
                        continue
                    rank = BORROWED.get(field.origin, len(BORROWED) + 1)
                    if rank < len(BORROWED) + 1 and (best is None or rank < best[0]):
                        best = (rank, ref, field)
            if best is not None:
                break
        if best is None:
            return Field(value, "model"), ""
        _, ref, field = best
        origin = "extracted" if not exact and field.origin == "database" else field.origin
        return Field(value, origin, source_id=field.source_id), ref


def _clip(value: str, limit: int = 300) -> str:
    """Summaries are for orientation; long values (a full judgment text) stay out."""
    return value if len(value) <= limit else value[:limit] + "…"


def field_value(record: Record, name: str) -> str:
    field = record.fields.get(name)
    return field.value if field else ""
