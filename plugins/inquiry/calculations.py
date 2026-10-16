"""Small, reproducible descriptive calculations over actual quoted inputs.

These checks establish arithmetic and input provenance. They do not establish
that the numbers measure comparable populations, causation, or probability.
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.tool_contracts import Record


class Calculation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation: Literal["percent_change", "difference", "ratio"]
    label: str = Field(default="", max_length=150, description="Short descriptive label; not a causal conclusion.")
    before: Decimal = Field(description="Baseline/first value, copied as a whole number token from quote.")
    after: Decimal = Field(description="Later/second value, copied as a whole number token from quote.")
    quote: str = Field(min_length=1, max_length=4000, description="Exact original passage containing both numeric inputs, with their context.")
    source_ref: str = Field(default="", max_length=100, description="Actual source Record ref; omit only when quote is exact user wording.")

    @field_validator("before", "after", mode="before")
    @classmethod
    def finite_decimal(cls, value):
        if isinstance(value, bool):
            raise ValueError("Calculation inputs must be finite numbers, not booleans.")
        try:
            number = Decimal(str(value))
        except Exception as exc:
            raise ValueError("Calculation inputs must be finite numbers.") from exc
        if not number.is_finite() or len(number.as_tuple().digits) > 30 or not -30 <= number.as_tuple().exponent <= 30:
            raise ValueError("Use finite inputs with at most 30 digits and a decimal exponent between -30 and 30.")
        return number


# Boundaries prevent 10 from binding to 100, 1.10, an identifier or a fraction
# of a malformed thousands-separated token. Chinese prose may abut a number.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.,+\-−])[-+−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?(?![A-Za-z0-9_]|[.,]\d)")


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _numeric_bindings(spec):
    tokens = []
    for match in _NUMBER.finditer(spec.quote):
        token = match.group()
        # Avoid expensive arbitrary exponents even though tokens are only read.
        try:
            number = Decimal(token.replace(",", "").replace("−", "-"))
        except Exception:
            continue
        tokens.append((number, token, match.start(), match.end()))
    result = {}
    for name in ("before", "after"):
        match = next((item for item in tokens if item[0] == getattr(spec, name)), None)
        if match is None:
            found = [token for _, token, _, _ in tokens][:12]
            raise ValueError(f"Calculation {name}={getattr(spec, name)} must appear as a whole numeric token in the exact quote. "
                             f"Numbers in this quote: {found}. Inputs must be copied from the quote, not derived "
                             "(to compare two changes, calculate each change separately from its own quote).")
        _, token, start, end = match
        result[name] = {"token": token, "quote_start": start, "quote_end": end}
    return result


def _covered_receipts(ctx, ref, field_name, field, start, end):
    fingerprint = ctx.records.fingerprint(ref)
    selected, spans = [], []
    for row in getattr(ctx.session, "tool_receipts", []):
        if (row.get("status") != "ok" or row.get("executed") is False
                or not any(isinstance(item, dict) and item.get("ref") == ref
                           and item.get("fingerprint") == fingerprint for item in row.get("refs", []))):
            continue
        if row.get("category") in {"source", "extract"} and row.get("id"):
            return [row["id"]]
        if row.get("category") != "read" or not row.get("id"):
            continue
        for part in row.get("observations") or []:
            if not isinstance(part, dict):
                continue
            left, right = part.get("offset"), part.get("end")
            if (part.get("ref") != ref or part.get("field") != field_name
                    or part.get("origin") != field.origin or type(left) is not int or type(right) is not int
                    or not 0 <= left < right <= len(field.value)
                    or part.get("text_sha256") != _digest(field.value[left:right])):
                continue
            spans.append((left, right, row["id"]))
    cursor = start
    for left, right, receipt_id in sorted(spans):
        if right <= cursor:
            continue
        if left > cursor:
            break
        cursor = max(cursor, right)
        if receipt_id not in selected:
            selected.append(receipt_id)
        if cursor >= end:
            return selected
    return []


def bind_quote(ctx, quote, source_ref="", field=""):
    """Bind an exact quote to server-owned user text or read original material."""
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("An evidence quote must contain original text.")
    if not source_ref:
        if field:
            raise ValueError("A field selector requires an actual source_ref.")
        for index, text in reversed(list(enumerate(ctx.user_texts()))):
            if isinstance(text, str) and quote in text:
                start = text.index(quote)
                return {"kind": "user_reported", "message_index": index,
                        "text_sha256": _digest(text), "start": start, "end": start + len(quote),
                        "quote": quote, "quote_sha256": _digest(quote), "receipt_ids": []}
        raise ValueError("The quote must be exact server-owned user wording, or name its actual source_ref.")
    record = ctx.records.get_exact(source_ref, Record)
    occurrences = 0
    for name, original in record.fields.items():
        if (field and field != name) or original.origin not in {"database", "extracted", "user"}:
            continue
        first = original.value.find(quote)
        if first >= 0:
            occurrences += 1 + int(original.value.find(quote, first + 1) >= 0)
    if occurrences != 1:
        raise ValueError("The quote must uniquely identify one original source passage; include more context or select its field.")
    for name, original in record.fields.items():
        if (field and field != name) or original.origin not in {"database", "extracted", "user"}:
            continue
        start = original.value.find(quote)
        while start >= 0:
            end = start + len(quote)
            receipts = _covered_receipts(ctx, source_ref, name, original, start, end)
            if receipts:
                return {"kind": "source_bound" if original.origin != "user" else "user_reported",
                        "source_ref": source_ref, "fingerprint": ctx.records.fingerprint(source_ref),
                        "field": name, "origin": original.origin, "text_sha256": _digest(original.value),
                        "start": start, "end": end, "quote": quote,
                        "quote_sha256": _digest(quote), "receipt_ids": receipts}
            start = original.value.find(quote, start + 1)
    raise ValueError("The quote needs an original Record field and a matching acquisition or read receipt covering the entire quote.")


def _whole_source_numbers(ctx, provenance, bindings):
    """A quote must not crop 100 out of 1000 and turn it into an input."""
    if provenance.get("source_ref"):
        text = ctx.records.get_exact(provenance["source_ref"], Record).fields[provenance["field"]].value
    else:
        text = ctx.user_texts()[provenance["message_index"]]
    spans = {(match.start(), match.end()) for match in _NUMBER.finditer(text)}
    for binding in bindings.values():
        if (provenance["start"] + binding["quote_start"], provenance["start"] + binding["quote_end"]) not in spans:
            raise ValueError("Calculation quotes must retain complete numeric tokens from the original material.")


def _text(value):
    # Fixed notation keeps JSON and rendered output reproducible; avoid -0.
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if value == 0 else result


def evaluate_calculations(ctx, specs):
    """Validate all inputs and return JSON-safe results without changing state."""
    if not isinstance(specs, (list, tuple)) or len(specs) > 4:
        raise ValueError("Supply at most four descriptive calculations.")
    results = []
    for raw in specs:
        spec = Calculation.model_validate(raw.model_dump() if isinstance(raw, Calculation) else raw)
        numeric_bindings = _numeric_bindings(spec)
        provenance = bind_quote(ctx, spec.quote, spec.source_ref)
        _whole_source_numbers(ctx, provenance, numeric_bindings)
        if spec.operation in {"percent_change", "ratio"} and spec.before == 0:
            raise ValueError("A ratio or percent change requires a nonzero baseline.")
        # Enough precision for the bounded input values and ordinary ratios;
        # repeating decimals are explicitly described as rounded.
        with localcontext() as decimal_context:
            decimal_context.prec = 100
            delta = spec.after - spec.before
            result = {"operation": spec.operation, "label": spec.label,
                      "before": _text(spec.before), "after": _text(spec.after),
                      "provenance": provenance, "numeric_bindings": numeric_bindings,
                      "receipt_ids": provenance["receipt_ids"], "descriptive_only": True,
                      "semantic_verification": False, "decimal_precision": 100}
            if spec.operation in {"difference", "percent_change"}:
                result["delta"] = _text(delta)
            if spec.operation == "percent_change":
                result["percent_change"] = _text(delta / spec.before * 100)
            elif spec.operation == "ratio":
                result["ratio"] = _text(spec.after / spec.before)
        results.append(result)
    return results


def _display(value):
    number = Decimal(value)
    with localcontext() as decimal_context:
        decimal_context.prec = 110
        rounded = number.quantize(Decimal("0.0001"))
    return ("≈" if rounded != number else "") + _text(rounded)


def render_calculations(results, zh=True):
    """Render arithmetic from validated results, never the model's label prose."""
    if not results:
        return ""
    lines = []
    for result in results:
        before, after = result["before"], result["after"]
        if result["operation"] == "percent_change":
            body = (f"{before} → {after}；差值 {result['delta']}；变化率 "
                    f"({after} − {before}) ÷ {before} × 100% = {_display(result['percent_change'])}%") if zh else (
                    f"{before} → {after}; difference {result['delta']}; percent change "
                    f"({after} − {before}) / {before} × 100% = {_display(result['percent_change'])}%")
        elif result["operation"] == "difference":
            body = f"{after} − {before} = {result['delta']}"
        else:
            body = f"{after} ÷ {before} = {_display(result['ratio'])}"
        origin = result["provenance"]
        provenance_label = ("用户提供数值" if zh else "user-provided inputs") if origin["kind"] == "user_reported" else origin["source_ref"]
        lines.append(f"- {body}（{provenance_label}）" if zh else f"- {body} ({provenance_label})")
    heading = "已核算（仅描述性计算，不证明因果、概率或数据可比性）：" if zh else "Calculated results (descriptive only; do not establish causation, probability, or comparability):"
    return heading + "\n\n" + "\n".join(lines)
