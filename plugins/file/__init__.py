"""Extract fields from an uploaded document, as an extracted record.

Everything the file yields -- its metadata, its text -- is ``extracted``
origin with the attachment as the record id: it came from the document,
not from a database, so citations from it are honest about being
unverified. Office documents and PDF text layers are read deterministically;
images and scanned pages use vision transcription with explicit coverage.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field as PField

from core.tool_contracts import Field, Record
from harness.plugin import Plugin, Result, Tool

# The extractor's deterministic fields; empty ones are dropped.
_FIELDS = ("title", "author", "date", "publisher")


class ExtractParams(BaseModel):
    attachment_id: str = PField(min_length=4, max_length=40, description="The uploaded file, e.g. att_1a2b3c4d")
    start_page: int | None = PField(default=None, ge=1, description="PDF: first page to extract, one-based; use missing_units to continue OCR")
    end_page: int | None = PField(default=None, ge=1, description="PDF: last page to extract, inclusive")


def extract(ctx, p: ExtractParams) -> Result:
    from local_tools.file_extractor import extract_from_file
    attachment = ctx.attachment(p.attachment_id)
    if attachment is None:
        raise ValueError("No such attachment; please upload the file first.")
    if p.start_page is not None or p.end_page is not None:
        data = extract_from_file(attachment["path"], start_page=p.start_page, end_page=p.end_page)
    else:
        data = extract_from_file(attachment["path"])
    if not isinstance(data, dict):
        raise ValueError("Could not read any content from this file.")
    if data.get("error"):
        return Result({"error": str(data["error"]), "kind": "document_extraction", "retryable": True,
                       "coverage": data.get("coverage", {"status": "failed"})})
    # An attachment is immutable in the app. A digest also prevents accidentally
    # merging page extractions if its backing bytes were changed externally.
    digest = hashlib.sha256(Path(attachment["path"]).read_bytes()).hexdigest()
    previous_ref, previous = next(((ref, r) for ref, r in reversed(ctx.records.all(Record))
                                  if r.provider == "file" and r.record_id == p.attachment_id), (None, None))
    if previous and data.get("coverage", {}).get("format") == "pdf" and \
            previous.fields.get("source_sha256") and previous.fields["source_sha256"].value == digest:
        data = _merge_pdf(previous, data)
    values = {name: str(data.get(name) or "").strip() for name in _FIELDS}
    values["title"] = values["title"] or str(data.get("page_title") or "").strip()
    # Offsets refer to the exact extractor text; trimming here would shift them.
    text = str(data.get("raw_text") or "")
    if not text.strip() and not any(values.values()):
        if data.get("coverage", {}).get("status") == "partial":
            return Result({"error": "Document extraction is incomplete; inspect coverage and retry missing pages.",
                           "kind": "document_extraction", "retryable": True, "coverage": data["coverage"]})
        raise ValueError("No extractable text in this file (it may be a scan or empty).")
    values["text"] = text
    values["source_sha256"] = digest
    coverage = data.get("coverage", {"status": "partial", "reason": "extractor_did_not_report_coverage"})
    values["extraction_coverage"] = json.dumps(coverage, ensure_ascii=False, sort_keys=True)
    values["extraction_segments"] = json.dumps(data.get("segments", []), ensure_ascii=False)
    fields = {name: Field(value, "extracted") for name, value in values.items() if value}
    record = Record("document", fields, "file", p.attachment_id)
    ref = ctx.save(record, {"attachment_id": p.attachment_id, "extraction_coverage": coverage}, supersedes=previous_ref)
    rows = [["File", attachment["name"]], ["Record", ref]]
    rows += [[name, values[name][:120]] for name in _FIELDS if values.get(name)]
    rows.append(["Text characters", str(len(text))])
    rows.append(["Extraction coverage", coverage["status"]])
    if coverage.get("missing_units"):
        rows.append(["Unprocessed / incomplete pages", ", ".join(str(n) for n in coverage["missing_units"][:30])])
    preview = text[:3000]
    return Result(
        {"ref": ref, "replaces": previous_ref, "fields": {name: values[name] for name in _FIELDS if values.get(name)},
         "preview": preview, "preview_truncated": len(preview) < len(text), "text_chars": len(text),
         "coverage": coverage,
         "note": "All extracted text is stored on the record; use record read/search to inspect it. "
                 "Extraction coverage describes processed content, not legal completeness or OCR accuracy."},
        [{"type": "card", "title": "File extracted", "rows": rows,
          "note": "Fields come from the file itself, sourced as \"extracted\", not marked verified -- "
                  "check them before citing."}], final=True)


def _merge_pdf(previous: Record, data: dict) -> dict:
    """Continue bounded OCR without discarding pages extracted earlier."""
    from local_tools.file_extractor import assemble_segments
    try:
        old_coverage = json.loads(previous.fields["extraction_coverage"].value)
        old_segments = json.loads(previous.fields["extraction_segments"].value)
        old_text = previous.fields["text"].value
    except (KeyError, ValueError, TypeError):
        return data
    coverage = data["coverage"]
    if old_coverage.get("format") != "pdf" or old_coverage.get("total_units") != coverage.get("total_units"):
        return data
    parts = {s["page"]: {**s, "text": old_text[s["start"]:s["end"]]}
             for s in old_segments if s.get("page")}
    for segment in data.get("segments", []):
        page = segment.get("page")
        if not page:
            continue
        # An incomplete retry must not replace an already complete page.
        if page in parts and parts[page].get("complete", True) and not segment.get("complete", True):
            continue
        parts[page] = {**segment, "text": data["raw_text"][segment["start"]:segment["end"]]}
    text, segments = assemble_segments([parts[p] for p in sorted(parts)])
    processed = sorted(set(old_coverage.get("processed_units", [])) | set(coverage.get("processed_units", [])))
    missing = sorted(set(range(1, coverage["total_units"] + 1)) - set(processed))
    errors = [e for e in old_coverage.get("errors", []) + coverage.get("errors", []) if e.get("page") not in processed]
    merged = {**old_coverage, **coverage, "status": "partial" if missing else "complete",
              "processed_units": processed, "missing_units": missing, "errors": errors}
    return {**data, "title": data.get("title") or previous.fields.get("title", Field("", "extracted")).value,
            "raw_text": text, "segments": segments, "coverage": merged}


PLUGIN = Plugin(
    name="file",
    title="File extraction",
    description="Extract the fields and text of an uploaded PDF/DOCX/PPTX/XLSX or image as an extracted record.",
    tools=[Tool("extract", "Extract fields and text from an uploaded file.", ExtractParams, extract)],
    category="extract",
    fact_patterns=(),
    default_enabled=True,
)
