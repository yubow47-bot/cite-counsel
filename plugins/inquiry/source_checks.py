"""Track explicitly requested URLs against actual source executions.

``retrieved`` means that original source text was obtained/read. It does not
mean the page is relevant, exhaustive, authoritative or supports a conclusion.
Callers choose the current question's user messages; unrelated older questions
must not silently acquire new source obligations. This module performs no I/O
and changes neither the store nor the session.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from core.tool_contracts import Record


_URL = re.compile(r"https?://[^\s<>\"'`“”‘’，。；：！？]+", re.IGNORECASE)
_BODY_FIELDS = {"text", "full_text", "content"}
_SOURCE_ORIGINS = {"database", "extracted"}


def normalize_url(value: str) -> str:
    """Normalize transport spelling only; preserve distinct paths and queries."""
    try:
        parts = urlsplit(value.strip())
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return ""
        host = parts.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        port = parts.port
        if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
            host += ":" + str(port)
        return urlunsplit((scheme, host, parts.path or "/", parts.query, ""))
    except (AttributeError, TypeError, ValueError):
        return ""


def _urls(text):
    for match in _URL.finditer(text if isinstance(text, str) else ""):
        value = match.group().rstrip(".,;:!?")
        # Markdown/prose delimiters are not part of the URL; balanced path
        # parentheses (e.g. an encyclopedia article) must remain intact.
        while value and value[-1] in ")]}":
            close = value[-1]
            if value.count(close) <= value.count({")": "(", "]": "[", "}": "{"}[close]):
                break
            value = value[:-1]
        url = normalize_url(value)
        if url:
            yield url


def required_urls(*, user_texts=(), premises=()):
    """Ordered, deduplicated explicit URLs, with no inferred websites."""
    urls = dict.fromkeys(url for text in user_texts for url in _urls(text))
    for premise in premises:
        value = premise.model_dump() if hasattr(premise, "model_dump") else premise
        if isinstance(value, Mapping):
            urls.update(dict.fromkeys(_urls(value.get("next_check", ""))))
    return list(urls)


def _record_urls(record, metadata):
    field = record.fields.get("url")
    values = [field.value] if field and field.origin in _SOURCE_ORIGINS else []
    snapshot = metadata.get("source_snapshot") or {}
    if isinstance(snapshot, Mapping):
        values.extend((snapshot.get("requested_url"), snapshot.get("final_url")))
    # The built-in web adapter uses its request URL as its record ID, including
    # legacy snapshots which did not retain a url field.
    if record.provider == "web":
        values.append(record.record_id)
    return {url for value in values if (url := normalize_url(value))}


def _has_body(record):
    return any(name in _BODY_FIELDS and field.origin in _SOURCE_ORIGINS and field.value.strip()
               for name, field in record.fields.items())


def _valid_read(record, ref, receipt):
    for observation in receipt.get("observations") or []:
        if not isinstance(observation, Mapping) or observation.get("ref") != ref:
            continue
        field = record.fields.get(observation.get("field"))
        start, end = observation.get("offset"), observation.get("end")
        if (observation.get("field") in _BODY_FIELDS and field is not None
                and field.origin in _SOURCE_ORIGINS and observation.get("origin") == field.origin
                and type(start) is int and type(end) is int and 0 <= start < end <= len(field.value)
                and hashlib.sha256(field.value[start:end].encode()).hexdigest() == observation.get("text_sha256")):
            return True
    return False


def _retrieved(store, receipt):
    """Yield (URL, material ref, binding) for current successful source reads."""
    if (receipt.get("executed") is not True or receipt.get("status") != "ok"
            or receipt.get("category") not in {"source", "extract", "read"}):
        return
    records = {}
    for item in receipt.get("refs") or []:
        if not isinstance(item, Mapping):
            continue
        ref = item.get("ref")
        try:
            record = store.get_exact(ref, Record)
            metadata = store.meta(ref)
            if (store.fingerprint(ref) == item.get("fingerprint") and not metadata.get("composed")
                    and store.current_ref(ref) == ref):
                records[ref] = (record, metadata)
        except (KeyError, TypeError, ValueError):
            continue
    for ref, (record, metadata) in records.items():
        material_ref = ref
        if not _has_body(record):
            # Same-content fetches retain a new raw response and point to the
            # original material. Both exact refs must be in this execution.
            material_ref = (metadata.get("web_source") or {}).get("material_ref")
            material = records.get(material_ref)
            if receipt.get("category") == "read" or material is None or not _has_body(material[0]):
                continue
        elif receipt.get("category") == "read" and not _valid_read(record, ref, receipt):
            continue
        for url in _record_urls(record, metadata):
            yield url, material_ref, "read" if receipt.get("category") == "read" else "acquired"


def _content(value):
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, Mapping) else {}
        except (TypeError, ValueError):
            pass
    return {}


def _failed_attempts(messages, receipts):
    """Resolve only executed fetch failures, never a model's claimed attempt.

    Receipts currently omit request arguments. The trusted tool-result receipt
    ID binds the URL in its preceding structured call to the server's receipt.
    If trimmed history no longer contains that pair, report unvisited rather
    than inventing a successful or failed attempt.
    """
    by_id = {row.get("id"): row for row in receipts if isinstance(row, Mapping) and row.get("id")}
    pending = {}
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, Mapping) or not call.get("id"):
                    continue
                function = call.get("function") or {}
                if function.get("name") == "web__fetch":
                    url = normalize_url(_content(function.get("arguments")).get("url"))
                    if url:
                        pending[call["id"]] = url
        elif message.get("role") == "tool":
            url = pending.pop(message.get("tool_call_id"), None)
            embedded = _content(message.get("content")).get("_execution_receipt") or {}
            row = by_id.get(embedded.get("id")) if isinstance(embedded, Mapping) else None
            if (url and row and row.get("tool") == "web__fetch" and row.get("executed") is True
                    and row.get("status") == "error"):
                yield url, row["id"]


def source_report(store, receipts, *, user_texts=(), premises=(), messages=()):
    """Return deterministic URL acquisition status, never a semantic verdict.

    ``binding=acquired`` records acquisition of original text, whose tool output
    may be truncated. ``binding=read`` additionally binds an actual original
    body-text read range. Neither proves decisive passages were inspected.
    """
    receipts = list(receipts)
    rows = {url: {"url": url, "status": "unvisited", "refs": [], "receipt_ids": [], "binding": None}
            for url in required_urls(user_texts=user_texts, premises=premises)}
    for receipt in receipts:
        if not isinstance(receipt, Mapping) or not receipt.get("id"):
            continue
        for url, ref, binding in _retrieved(store, receipt):
            if url not in rows:
                continue
            row = rows[url]
            row["status"] = "retrieved"
            if row["binding"] != "read":
                row["binding"] = binding
            if ref not in row["refs"]:
                row["refs"].append(ref)
            if receipt["id"] not in row["receipt_ids"]:
                row["receipt_ids"].append(receipt["id"])
    for url, receipt_id in _failed_attempts(messages, receipts):
        if url in rows:
            row = rows[url]
            if row["status"] != "retrieved":
                row["status"] = "failed"
            if receipt_id not in row["receipt_ids"]:
                row["receipt_ids"].append(receipt_id)
    return list(rows.values())


def missing_required(report):
    """Failures remain missing; they permit an explicitly partial answer."""
    return [dict(row) for row in report if row.get("status") != "retrieved"]
