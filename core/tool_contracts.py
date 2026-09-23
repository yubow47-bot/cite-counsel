"""Evidence contracts shared by legal tools.

Grounding describes traceability, not legal correctness, rule applicability,
or search completeness. These objects are not a sandbox for untrusted plugins.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping
from typing import Literal

Origin = Literal["database", "user", "extracted", "computed"]


def _identifier(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class Field:
    value: str
    origin: Origin
    source_id: str | None = None
    rule_id: str | None = None
    span: tuple[int, int] | None = None
    derivation: Derivation | None = None

    def __post_init__(self):
        if not isinstance(self.value, str):
            raise ValueError("Field value must be text")
        if self.origin not in {"database", "user", "extracted", "computed"}:
            raise ValueError("Unknown field origin")
        for value in (self.source_id, self.rule_id):
            if value is not None and not _identifier(value):
                raise ValueError("Evidence identifiers must be nonempty text")
        if self.derivation is not None and not isinstance(self.derivation, Derivation):
            raise ValueError("Computation requires a Derivation")
        if self.span is not None:
            if not isinstance(self.span, (tuple, list)) or len(self.span) != 2:
                raise ValueError("Span must contain two offsets")
            start, end = self.span
            if type(start) is not int or type(end) is not int or start < 0 or end <= start:
                raise ValueError("Span must be a nonempty half-open range")
            object.__setattr__(self, "span", (start, end))
        if self.derivation is not None and self.origin != "computed":
            raise ValueError("Only computed fields carry a derivation")


@dataclass(frozen=True)
class Derivation:
    inputs: tuple[Field | Derivation, ...]
    rule_id: str
    input_names: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "input_names", tuple(self.input_names))
        if not isinstance(self.rule_id, str) or not self.rule_id.strip():
            raise ValueError("Derivation requires a versioned rule identifier")
        if any(not isinstance(node, (Field, Derivation)) for node in self.inputs):
            raise ValueError("Derivation inputs must be fields or derivations")
        if self.input_names and (len(self.input_names) != len(self.inputs)
                                 or any(not isinstance(name, str) or not name for name in self.input_names)
                                 or len(set(self.input_names)) != len(self.input_names)):
            raise ValueError("Input names must be unique and match the inputs")


@dataclass(frozen=True)
class Record:
    source_type: str
    fields: Mapping[str, Field]
    provider: str
    record_id: str

    def __post_init__(self):
        if not all(_identifier(value) for value in (self.source_type, self.provider, self.record_id)):
            raise ValueError("Record requires source type, provider and record ID")
        if not isinstance(self.fields, Mapping) or any(
            not _identifier(name) or not isinstance(value, Field)
            for name, value in self.fields.items()
        ):
            raise ValueError("Record fields must map names to Field objects")
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


@dataclass(frozen=True)
class Artifact:
    kind: str
    content: str | bytes
    derivation: Derivation

    def __post_init__(self):
        if not _identifier(self.kind) or not isinstance(self.content, (str, bytes)):
            raise ValueError("Artifact requires a kind and text or bytes content")
        if not isinstance(self.derivation, Derivation):
            raise ValueError("Artifact requires a Derivation")


@dataclass(frozen=True)
class Finding:
    verdict: Literal["confirmed", "contradicted", "inconclusive"]
    detail: str
    coverage: Literal["complete", "partial"]
    derivation: Derivation

    def __post_init__(self):
        if not isinstance(self.detail, str) or not isinstance(self.derivation, Derivation):
            raise ValueError("Finding requires text detail and a Derivation")
        if self.verdict not in {"confirmed", "contradicted", "inconclusive"}:
            raise ValueError("Unknown verdict")
        if self.coverage not in {"complete", "partial"}:
            raise ValueError("Unknown coverage")


@dataclass(frozen=True)
class GroundingIssue:
    path: tuple[str, ...]
    reason: str


def derivation_leaves(derivation: Derivation):
    """Every Field leaf of a derivation tree, iteratively and cycle-safe.

    Yields leaves in depth-first order; a cycle yields the node once per
    path taken (callers detect repetition by identity if they need to) and
    the walk stops after 20000 nodes, like ``grounding_issues``.
    """
    active: set[int] = set()
    stack = [derivation]
    visited = 0
    while stack:
        node = stack.pop()
        visited += 1
        if visited > 20000:
            raise ValueError("evidence_graph_too_large")
        if isinstance(node, Derivation):
            if id(node) in active:
                continue
            active.add(id(node))
            for index in reversed(range(len(node.inputs))):
                stack.append(node.inputs[index])
        elif isinstance(node, Field):
            yield node
            if node.origin == "computed" and node.derivation is not None:
                stack.append(node.derivation)


def grounding_issues(derivation: Derivation) -> tuple[GroundingIssue, ...]:
    """Explain every unsupported dependency; empty/cyclic proofs fail closed.

    Iterative traversal also supports deep document compositions. Source IDs
    are references supplied by trusted adapters, not proof of authenticity.
    """
    issues = []
    active = set()
    stack = [(derivation, (), False)]
    visited = 0
    while stack:
        node, path, leaving = stack.pop()
        if leaving:
            active.remove(id(node))
            continue
        visited += 1
        if visited > 20000:
            issues.append(GroundingIssue(path, "evidence_graph_too_large"))
            break
        if not isinstance(node, (Field, Derivation)):
            issues.append(GroundingIssue(path, "invalid_node"))
            continue
        if id(node) in active:
            issues.append(GroundingIssue(path, "cyclic_derivation"))
            continue
        active.add(id(node))
        stack.append((node, path, True))
        if isinstance(node, Derivation):
            if not node.inputs:
                issues.append(GroundingIssue(path, "empty_derivation"))
            for index in reversed(range(len(node.inputs))):
                name = node.input_names[index] if node.input_names else str(index)
                stack.append((node.inputs[index], path + (name,), False))
        elif node.origin == "database":
            if not isinstance(node.source_id, str) or not node.source_id.strip():
                issues.append(GroundingIssue(path, "missing_source_id"))
        elif node.origin == "computed":
            if node.derivation is None:
                issues.append(GroundingIssue(path, "missing_computation_inputs"))
            else:
                if node.rule_id != node.derivation.rule_id:
                    issues.append(GroundingIssue(path, "computation_rule_mismatch"))
                stack.append((node.derivation, path + ("computation",), False))
        else:
            issues.append(GroundingIssue(path, "unverified_" + node.origin))
    return tuple(issues)


def is_grounded(derivation: Derivation) -> bool:
    return not grounding_issues(derivation)
