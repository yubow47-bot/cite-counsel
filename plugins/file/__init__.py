"""Extract fields from an uploaded document, as an extracted record.

Everything the file yields -- its metadata, its text -- is ``extracted``
origin with the attachment as the record id: it came from the document,
not from a database, so citations from it are honest about being
unverified. The type comes from the file's own text; no model call sits
between the bytes and the record.
"""

from __future__ import annotations

from pydantic import BaseModel, Field as PField

from core.tool_contracts import Field, Record
from harness.plugin import Plugin, Result, Tool

# The extractor's deterministic fields; empty ones are dropped.
_FIELDS = ("title", "author", "date", "publisher")


class ExtractParams(BaseModel):
    attachment_id: str = PField(min_length=4, max_length=40, description="The uploaded file, e.g. att_1a2b3c4d")


def extract(ctx, p: ExtractParams) -> Result:
    from local_tools.file_extractor import extract_from_file
    attachment = ctx.attachment(p.attachment_id)
    if attachment is None:
        raise ValueError("No such attachment; please upload the file first.")
    data = extract_from_file(attachment["path"])
    if not isinstance(data, dict):
        raise ValueError("Could not read any content from this file.")
    values = {name: str(data.get(name) or "").strip() for name in ("title", "author", "date", "publisher")}
    text = str(data.get("raw_text") or "").strip()
    if not text and not any(values.values()):
        raise ValueError("No extractable text in this file (it may be a scan or empty).")
    values["text"] = text
    fields = {name: Field(value, "extracted") for name, value in values.items() if value}
    record = Record("document", fields, "file", p.attachment_id)
    ref = ctx.save(record)
    rows = [["File", attachment["name"]], ["Record", ref]]
    rows += [[name, value[:120]] for name, value in values.items() if value and name != "text"]
    rows.append(["Text characters", str(len(text))])
    return Result(
        {"ref": ref, "fields": {name: field.value for name, field in record.fields.items()},
         "note": "an extracted record: fields come from the file, not from a database"},
        [{"type": "card", "title": "File extracted", "rows": rows,
          "note": "Fields come from the file itself, sourced as \"extracted\", not marked verified -- "
                  "check them before citing."}], final=True)


PLUGIN = Plugin(
    name="file",
    title="File extraction",
    description="Extract the fields and text of an uploaded PDF/DOCX/PPTX/XLSX or image as an extracted record.",
    instructions="Extracted records (rec_N) carry fields from the file itself. They are not database records: "
                 "citations from them will read unverified, which is honest. For court decisions, prefer the "
                 "case databases; the file record is for what they do not have.",
    tools=[Tool("extract", "Extract fields and text from an uploaded file.", ExtractParams, extract)],
    category="extract",
    fact_patterns=(),
    default_enabled=True,
)