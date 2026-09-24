"""A McGill bibliography from stored citation artifacts.

The tool accepts citation artifact numbers only; the entries come from
the store's sidecar (the exact fields each citation was rendered from),
so an entry's verification status is always re-derived from the field
origins -- and a user-typed pinpoint that the bibliography omits does
not cost the entry its verification.
"""

from __future__ import annotations

from pydantic import BaseModel, Field as PField

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
            raise ValueError(f"{ref} is not a rendered citation; render one with the citation plugin first.")
        meta = ctx.records.meta(ref)
        if not meta:
            raise ValueError(f"{ref} has no source snapshot to draw on, so it cannot join the bibliography.")
        entries.append(meta)
    result = bibliography.build(entries)
    ref = ctx.save(result["artifact"])
    rows = []
    for section in result["sections"]:
        for entry in section["entries"]:
            rows.append([section["title"], ("✓ " if entry["verified"] else "○ ") + entry["text"]])
    return Result(
        {"ref": ref, "count": result["count"], "unverified": result["unverified"],
         "grounded": result["grounded"],
         "note": "the bibliography is stored as an artifact; every entry's verification was re-derived"},
        [{"type": "card", "title": f"Bibliography ({result['count']} entries)",
          "rows": rows[:60], "body": result["text"],
          "note": ("Every entry is verified." if result["unverified"] == 0
                   else f"{result['unverified']} entries are unverified (marked ○): a field in them "
                        f"is not from a database.")}],
        final=True)


PLUGIN = Plugin(
    name="bibliography",
    title="Bibliography",
    description="Assemble stored citations into a McGill bibliography: sections, sorted, pinpoints dropped.",
    instructions="Bibliography takes citation artifact numbers (art_N) from the mcgill plugin. Show the sections "
                 "and the unverified count; the card carries the text to copy.",
    tools=[Tool("build", "Build a McGill bibliography from stored citations.", BuildParams, build)],
    category="function",
    fact_patterns=(),
    default_enabled=True,
)