"""Harness-issued execution receipts, independent of a model's investigation claims."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from core.tool_contracts import Record

REF = re.compile(r"(?:rec|art|fnd)_\d{1,12}\Z")


def referenced_objects(value, store):
    """Read explicit structured refs; source prose cannot manufacture references."""
    found = []

    def walk(node, key="", depth=0):
        if depth > 8 or len(found) >= 50:
            return
        if isinstance(node, str) and REF.fullmatch(node) and (key == "ref" or key.endswith("_ref") or key in {"refs", "source_refs"}):
            if node not in found:
                try:
                    store.get_exact(node)
                    found.append(node)
                except ValueError:
                    pass
        elif isinstance(node, dict):
            for name, child in node.items():
                if name not in {"fields", "field_values", "raw", "raw_data", "body", "text", "full_text", "metadata"}:
                    walk(child, name, depth + 1)
        elif isinstance(node, (list, tuple)):
            for child in node[:100]:
                walk(child, key, depth + 1)

    walk(value)
    return [{"ref": ref, "fingerprint": store.fingerprint(ref)} for ref in found]


def issue(session, plugins, name, content):
    plugin_name = name.partition("__")[0]
    plugin = plugins.get(plugin_name)
    category = plugin.category if plugin else "core"
    failed = isinstance(content, dict) and "error" in content
    empty = isinstance(content, dict) and not failed and (
        content.get("found") == 0 or content.get("records") == [] or content.get("results") == [])
    refs = referenced_objects(content, session.records)
    observations = []
    if name in {"record__read", "record__search"} and not failed:
        # A read of saved source material is real evidence inspection. Reading
        # a generated artifact is useful, but is not source investigation.
        source_refs = []
        for item in refs:
            obj = session.records.get_exact(item["ref"])
            slices = content.get("matches", []) if name == "record__search" else [content]
            read_original = any(isinstance(part, dict) and part.get("ref") == item["ref"]
                                and part.get("origin") in {"database", "extracted", "user"}
                                and isinstance(part.get("text"), str) and bool(part["text"].strip())
                                for part in slices)
            if isinstance(obj, Record) and read_original:
                source_refs.append(item)
                for part in slices:
                    if not isinstance(part, dict) or part.get("ref") != item["ref"] or not part.get("text", "").strip():
                        continue
                    observations.append({"ref": item["ref"], "field": part.get("field"),
                                         "offset": part.get("offset", part.get("context_start", part.get("start"))),
                                         "end": part.get("offset", 0) + len(part["text"]) if "offset" in part else part.get("context_end", part.get("end")),
                                         "match_start": part.get("start"), "match_end": part.get("end"),
                                         "total_chars": part.get("total_chars"), "origin": part.get("origin"),
                                         "text_sha256": hashlib.sha256(part["text"].encode()).hexdigest()})
        if source_refs:
            category, refs = "read", source_refs
    coverage = content.get("coverage") if isinstance(content, dict) else None
    if coverage is None:
        coverages = [session.records.meta(item["ref"]).get("source_coverage") for item in refs]
        coverage = next((c for c in coverages if c), None)
    receipt = {
        "id": "run_" + uuid.uuid4().hex,
        "sequence": len(session.tool_receipts) + 1,
        "tool": name,
        "category": category,
        "status": "error" if failed else "no_match" if empty and category in {"source", "extract"} else "ok",
        "executed": not (isinstance(content, dict) and content.get("kind") in {
            "repeated_call", "unavailable_tool", "unavailable_plugin", "invalid_arguments", "resource_limit"}),
        "refs": refs,
        "observations": observations[:20],
        "source_refs": [item["ref"] for item in refs if item["ref"].startswith("rec_")],
        "coverage": coverage,
        "error_kind": content.get("kind") if failed else None,
        "result_sha256": hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest(),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    session.tool_receipts.append(receipt)
    return receipt
