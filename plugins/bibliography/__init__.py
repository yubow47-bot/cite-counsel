"""A McGill bibliography from stored citation artifacts.

The tool accepts citation artifact numbers only; the entries come from
the store's sidecar (the exact fields each citation was rendered from),
so an entry's verification status is always re-derived from the field
origins -- and a user-typed pinpoint that the bibliography omits does
not cost the entry its verification.
"""

from __future__ import annotations

from pydantic import BaseModel, Field as PField

from core.tool_contracts import Field, is_grounded
from harness.plugin import Plugin, Result, Tool


class BuildParams(BaseModel):
    refs: list[str] = PField(min_length=1, max_length=200,
                             description="Citation artifact refs to include, e.g. [\"art_1\", \"art_4\"]")


def build(ctx, p: BuildParams) -> Result:
    from core import bibliography
    entries = []
    for ref in p.refs:
        artifact = ctx.records.get(ref)
        if not artifact or getattr(artifact, "kind", "") != "citation_text":
            raise ValueError(f"{ref} is not a citation artifact produced by the citation plugin.")
        meta = ctx.records.meta(ref)
        if not meta:
            raise ValueError(f"{ref} has no source snapshot to draw on, so it cannot join the bibliography.")
        # Persisted snapshots from older versions may contain formatted values
        # or pre-audit origins. The stored citation proof is authoritative.
        audited = {name: node for name, node in zip(artifact.derivation.input_names, artifact.derivation.inputs)
                   if isinstance(node, Field)}
        fields = {}
        for name, field in meta.get("fields", {}).items():
            node = audited.get(name)
            fields[name] = ({"value": node.value, "origin": node.origin, "source_id": node.source_id}
                            if node else {**field, "origin": "model", "source_id": None})
        entries.append({**meta, "fields": fields})
    result = bibliography.build(entries)
    ref = ctx.save(result["artifact"])
    stored = ctx.records.get(ref)
    entries_after_audit = [entry for section in result["sections"] for entry in section["entries"]]
    for entry, proof in zip(entries_after_audit, stored.derivation.inputs):
        entry["verified"] = is_grounded(proof)
    result["unverified"] = sum(not entry["verified"] for entry in entries_after_audit)
    result["grounded"] = is_grounded(stored.derivation)
    rows = []
    for section in result["sections"]:
        for entry in section["entries"]:
            rows.append([section["title"], ("✓ " if entry["verified"] else "○ ") + entry["text"]])
    return Result(
        {"ref": ref, "count": result["count"], "unverified": result["unverified"],
         "grounded": result["grounded"], "source_grounded": result["grounded"], "legal_verification": False,
         "note": "The bibliography is stored. Database sourcing was re-derived for retained fields; "
                 "legal accuracy, authority and applicability have not been assessed."},
        [{"type": "card", "title": f"Bibliography ({result['count']} entries)",
          "rows": rows[:60], "body": result["text"],
          "note": (("Every entry's retained fields trace to database records." if result["unverified"] == 0
                    else f"{result['unverified']} {'entry has' if result['unverified'] == 1 else 'entries have'} "
                         "fields that do not trace to database records (marked ○).")
                   + " Legal accuracy, authority and applicability have not been assessed.")}],
        final=True)


PLUGIN = Plugin(
    name="bibliography",
    title="Bibliography",
    description="Assemble stored citations into a McGill bibliography: sections, sorted, pinpoints dropped.",
    tools=[Tool("build", "Build a McGill bibliography from stored citations.", BuildParams, build)],
    category="function",
    fact_patterns=(),
    default_enabled=True,
)
