"""Stable work identity and exact material fingerprints, without provenance upgrades.

The adapter's identifier alone cannot identify an edited citation record. Include
the recorded bibliographic identity so changing A's citation to B never keeps A's
locations. Text hashes refer to the exact stored version, not normalized prose.
"""

from __future__ import annotations

import hashlib
import json

from core.tool_contracts import Field, Record

_IDENTITY_FIELDS = {
    "jurisprudence": ("style_of_cause", "neutral_citation", "reporter", "court", "year", "date"),
    "legislation": ("title", "citation", "jurisdiction", "chapter", "year"),
    "bill": ("number", "title", "parliament", "session", "year"),
    "book": ("author", "title", "isbn", "edition", "year"),
    "journal_article": ("author", "title", "doi", "journal", "year", "volume", "issue", "first_page"),
    "constitutional": ("title", "jurisdiction", "citation", "part", "part_of", "enacted_as", "reprinted_in"),
    "by_law": ("name", "number", "year"),
    "treaty": ("name", "date", "citation"),
    "foreign": ("name", "citation", "year", "court"),
    "news_online": ("author", "title", "newspaper", "date", "site"),
}
_DEFAULT_IDENTITY_FIELDS = ("title", "author", "url", "citation", "number", "date", "year")


def identity_fields(source_type: str) -> tuple[str, ...]:
    return _IDENTITY_FIELDS.get(source_type, _DEFAULT_IDENTITY_FIELDS)


def work_identity(record: Record) -> dict:
    values = {name: record.fields[name].value for name in identity_fields(record.source_type)
              if name in record.fields}
    digest = hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"provider": record.provider, "record_id": record.record_id,
            "source_type": record.source_type, "identity_sha256": digest}


def text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def text_field(record: Record) -> tuple[str, Field] | None:
    for name in ("full_text", "text", "content"):
        field = record.fields.get(name)
        if field is not None and field.value.strip():
            return name, field
    return None
