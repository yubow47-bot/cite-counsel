"""Exact, attributed observations that can survive an uncertain conclusion.

An observation proves where quoted wording came from. It never promotes a
source statement, a user's report, or a model's interpretation to verified fact.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from plugins.inquiry.calculations import bind_quote


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref: str = Field(default="", max_length=100, description="Exact source Record ref. Omit only for an exact quote from the user's actual messages.")
    quote: str = Field(min_length=1, max_length=1500, description="A short exact passage preserving the relevant qualifications. No paraphrase, inferred cause, or added explanation.")
    field: str = Field(default="", max_length=100, description="Original source field, normally text/full_text. Omit to locate the quote automatically; leave empty for user messages.")


def evaluate_observations(ctx, specs):
    """Validate all observations without persisting any model or source object."""
    if not isinstance(specs, (list, tuple)) or len(specs) > 6:
        raise ValueError("Supply at most six exact observations.")
    result = []
    for raw in specs:
        spec = Observation.model_validate(raw.model_dump() if isinstance(raw, Observation) else raw)
        if not spec.quote.strip():
            raise ValueError("An observation needs a nonempty exact quote.")
        if not spec.source_ref and spec.field:
            raise ValueError("User-message observations have no source field; omit field or supply the actual source_ref.")
        binding = bind_quote(ctx, spec.quote, source_ref=spec.source_ref, field=spec.field)
        # No free-form text is carried across this boundary. The rendered body
        # is exactly the source/user wording whose provenance was just bound.
        result.append({"quote": binding["quote"], "provenance": binding,
                       "receipt_ids": list(binding["receipt_ids"]),
                       "semantic_verification": False, "legal_verification": False})
    return result


def render_observations(results, zh=True):
    """Attribute literal wording; never render a model-supplied fact summary."""
    if not results:
        return ""
    lines = []
    for item in results:
        provenance = item["provenance"]
        if provenance["kind"] == "user_reported":
            label = "用户提供" if zh else "You provided"
        else:
            label = "来源记载" if zh else "The source states"
        ref = provenance.get("source_ref")
        if ref:
            label += f" ({ref})"
        # Prefix every line: an embedded newline cannot break out of the quote
        # and appear to be an unattributed assertion in the delivered answer.
        quoted = "\n".join("> " + line for line in provenance["quote"].splitlines())
        lines.extend([f"{label}：" if zh else f"{label}:", "", quoted, ""])
    return "\n".join(lines).rstrip()
