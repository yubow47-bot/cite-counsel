"""Immutable legal research objects and mechanical dependency inspection.

These objects preserve assertions; they do not validate their legal meaning.
An exact excerpt proves a character slice of a session record, not that the
speaker label, authority, or a claimed support relationship is correct.
"""

from __future__ import annotations

import hashlib
import json

from core.schema_migration import upgrade
from core.tool_contracts import Artifact, Record

SCHEMA = "legal_research.v1"
# Older schema -> function returning the next schema's dict. Empty until a v2 exists.
MIGRATIONS: dict = {}
KINDS = {"excerpt", "claim", "premise", "conclusion", "draft"}
SCOPE_FIELDS = ("jurisdiction", "as_of", "procedure", "factual_context")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unpack(artifact: Artifact) -> dict:
    if not isinstance(artifact, Artifact) or artifact.kind != "legal_research":
        raise ValueError("Expected a saved legal research object.")
    data = upgrade(json.loads(artifact.content), SCHEMA, MIGRATIONS, "legal research")
    if data.get("type") not in KINDS:
        raise ValueError("Unsupported legal research schema.")
    return data


def dependency(store, ref: str, role: str) -> dict:
    store.get_exact(ref)
    return {"ref": ref, "fingerprint": store.fingerprint(ref), "role": role}


def assertion_view(node: dict, ref: str, path: list[str]) -> dict:
    """Describe declared analysis without certifying its semantic content.

    Older saved assertions predate basis/scope fields. They retain their
    source-based support check and explicitly unknown scope at inspection.
    """
    scope = node.get("scope") or {}
    return {"ref": ref, "path": path, "type": node["type"], "text": node["text"],
            "status": node["status"], "basis_kind": node.get("basis_kind", "source"),
            "basis_verification": "asserted_only", "reasoning": node.get("reasoning", ""),
            "scope": {**{key: scope.get(key, "") for key in SCOPE_FIELDS},
                      "applicability": scope.get("applicability", "unknown"), "note": scope.get("note", "")},
            "scope_verification": "asserted_only",
            "evidence": [{**link, "relationship_verification": "asserted_only"} for link in node["evidence"]],
            "premises": node["premises"], "assessment": node["assessment"],
            "limitations": node.get("limitations", []), "counterargument": node.get("counterargument", ""),
            "counterargument_response": node.get("counterargument_response", ""),
            "semantic_verification": False, "legal_verification": False}


def validate_revision(store, prior: str | None, dependencies: list[dict]) -> None:
    """Reject stale edits and dependencies on the lineage being replaced.

    Exact-version edges form a DAG, but a revision that depends on its own
    previous version (even indirectly) becomes stale as soon as it is saved.
    Checking the current alias catches earlier versions in the same lineage.
    """
    if prior is None:
        return
    latest = store.current_ref(prior)
    if latest != prior:
        raise ValueError(f"Research revision conflict: {prior} was revised to {latest}. Read the current version before revising it.")
    seen, stack = set(), [dep["ref"] for dep in dependencies]
    while stack:
        ref = stack.pop()
        if ref in seen:
            continue
        seen.add(ref)
        if ref == prior or store.current_ref(ref) == prior:
            raise ValueError(f"Research revision would depend on the version it replaces ({prior}), directly or through {ref}. Remove that dependency first.")
        if len(seen) > 400:
            raise ValueError("Research revision dependency inspection exceeded 400 objects; reduce the graph before revising it.")
        obj = store.get_exact(ref)
        if isinstance(obj, Artifact) and obj.kind == "legal_research":
            stack.extend(dep["ref"] for dep in unpack(obj).get("dependencies", []))


def render_snapshot(store, title: str, refs: list[str]) -> str:
    """Render all exact research dependencies once, without following aliases.

    This is a static, portable snapshot. A separately generated inspection
    header reports the state at export time; neither rewrites saved objects.
    """
    lines = ["# " + title, "", "Research snapshot. Speaker labels, premise states and evidence relationships are assertions requiring legal review.",
             "Omitted basis means source; omitted scope is unknown. Legal verification: false.", ""]
    seen, stack, research_nodes = set(), list(reversed(refs)), 0
    while stack:
        ref = stack.pop()
        if ref in seen:
            continue
        seen.add(ref)
        try:
            obj = store.get_exact(ref)
            if not isinstance(obj, Artifact) or obj.kind != "legal_research":
                continue
            node = unpack(obj)
        except (ValueError, TypeError):
            lines.extend([f"Unavailable research dependency: {ref}.", ""])
            continue
        research_nodes += 1
        if research_nodes > 400:
            lines.extend(["DEPENDENCY SNAPSHOT INCOMPLETE: traversal exceeded 400 research objects.", ""])
            break
        lines.extend([f"## {node['type']} [{ref}]", "", f"Exact-version fingerprint: {store.fingerprint(ref)}", ""])
        if node["type"] == "excerpt":
            source = node["source"]
            lines.extend(["> " + node["quote"].replace("\n", "\n> "), "",
                          f"Speaker: {node['speaker']} (asserted only).",
                          f"Source: {source['ref']}.{source['field']}[{source['start']}:{source['end']}]; "
                          f"{source['work']['provider']}/{source['work']['record_id']} ({source['work']['source_type']}); "
                          f"origin: {source['origin']}.",
                          f"Source-version fingerprint: {source['fingerprint']}", ""])
        elif node["type"] == "draft":
            lines.extend([node["title"], "", "Assertion versions: " + ", ".join(node["claim_refs"]), ""])
        else:
            lines.extend([node["text"], "", f"State: {node['status']} (asserted). Assessment: {node['assessment']} (not legal verification).", ""])
            view = assertion_view(node, ref, [ref])
            if view["basis_kind"] != "source":
                lines.extend([f"Basis: {view['basis_kind']} (asserted only). Legal verification: false.", ""])
            if view["basis_kind"] == "assumption":
                lines.extend(["UNVERIFIED ASSUMPTION: a working premise, not an established fact.", ""])
            if view["reasoning"]:
                lines.extend(["Reasoning (asserted): " + view["reasoning"], ""])
            scope = view["scope"]
            if any(scope[key] for key in SCOPE_FIELDS) or scope["applicability"] != "unknown" or scope["note"]:
                lines.extend(["Declared scope (unverified): " + "; ".join(
                    f"{key}: {scope[key] or 'unknown'}" for key in SCOPE_FIELDS)
                    + f"; applicability: {scope['applicability']}.", ""])
            if scope["note"]:
                lines.extend(["Applicability note (asserted): " + scope["note"], ""])
            for limitation in view["limitations"]:
                lines.extend(["Limitation/open check (asserted): " + limitation, ""])
            if view["counterargument"]:
                lines.extend(["Counterargument (asserted): " + view["counterargument"], ""])
            if view["counterargument_response"]:
                lines.extend(["Response to counterargument/evidence (asserted): " + view["counterargument_response"], ""])
            for link in node["evidence"]:
                lines.extend([f"Evidence {link['relation']} (asserted): [{link['excerpt_ref']}] {link['note']}", ""])
            for premise in node["premises"]:
                lines.extend([f"Depends on premise/assertion [{premise}].", ""])
        stack.extend(dep["ref"] for dep in reversed(node.get("dependencies", [])))
    return "\n".join(lines)


def inspect(store, ref: str, reviews: dict | None = None) -> dict:
    """Inspect exact versions, propagating changes through all dependencies.

    Never resolves a saved dependency to its replacement. ``current_ref`` is
    used only to report the replacement and invalidate review. Review is a
    user's acknowledgement of a snapshot, never a legal correctness verdict.
    """
    data = unpack(store.get_exact(ref, Artifact))
    issues, sources, assertions, seen = [], [], [], set()
    stack = [(ref, [ref])]
    while stack:
        current, path = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if len(seen) > 400:
            issues.append({"code": "inspection_limit", "ref": current, "path": path})
            break
        try:
            node = unpack(store.get_exact(current, Artifact))
        except (ValueError, TypeError):
            issues.append({"code": "missing_research_object", "ref": current, "path": path})
            continue
        latest = store.current_ref(current)
        if latest != current:
            issues.append({"code": "object_revised", "ref": current, "current_ref": latest, "path": path})
        if node["type"] == "excerpt":
            source = node["source"]
            sources.append({**source, "excerpt_ref": current, "quote": node["quote"],
                            "speaker": node["speaker"], "speaker_verification": "asserted_only"})
            try:
                record = store.get_exact(source["ref"], Record)
                field = record.fields.get(source["field"])
                work = {"provider": record.provider, "record_id": record.record_id,
                        "source_type": record.source_type}
                valid = (field is not None and work == source["work"]
                         and store.fingerprint(source["ref"]) == source["fingerprint"]
                         and digest(field.value) == source["field_sha256"]
                         and field.value[source["start"]:source["end"]] == node["quote"]
                         and digest(node["quote"]) == source["quote_sha256"])
                if not valid:
                    issues.append({"code": "excerpt_binding_mismatch", "ref": current, "path": path})
            except (ValueError, TypeError):
                issues.append({"code": "source_unavailable", "ref": source["ref"], "path": path})
        elif node["type"] != "draft":
            assertion = assertion_view(node, current, path)
            assertions.append(assertion)
            basis_kind = assertion["basis_kind"]
            if basis_kind == "assumption":
                issues.append({"code": "unverified_assumption", "ref": current, "path": path,
                               "note": "This is an explicitly unverified working premise, regardless of its asserted status."})
            elif basis_kind == "inference":
                missing = (["missing_premises"] if not assertion["premises"] else [])
                if not assertion["reasoning"].strip():
                    missing.append("missing_reasoning")
                if missing:
                    issues.append({"code": "inference_gap", "ref": current, "path": path, "missing": missing})
            elif not any(link["relation"] == "supports" for link in node["evidence"]):
                issues.append({"code": "support_gap", "ref": current, "path": path})
            if node["status"] in {"unknown", "disputed"}:
                issues.append({"code": "unresolved_" + node["status"], "ref": current, "path": path})
            if any(link["relation"] == "opposes" for link in node["evidence"]):
                issues.append({"code": "opposing_evidence", "ref": current, "path": path})
            if ((assertion["counterargument"].strip() or any(link["relation"] == "opposes" for link in node["evidence"]))
                    and not assertion["counterargument_response"].strip()):
                issues.append({"code": "unhandled_counterevidence", "ref": current, "path": path,
                               "note": "An identified counterargument or opposing passage has no recorded response."})
            if assertion["scope"]["applicability"] == "not_applicable":
                issues.append({"code": "scope_not_applicable", "ref": current, "path": path,
                               "note": "This assertion is declared not applicable; applicability itself remains unverified."})
        for dep in node.get("dependencies", []):
            target = dep["ref"]
            dep_path = path + [target]
            try:
                obj = store.get_exact(target)
                if store.fingerprint(target) != dep["fingerprint"]:
                    issues.append({"code": "dependency_changed", "ref": target, "path": dep_path})
                latest = store.current_ref(target)
                if latest != target:
                    issues.append({"code": "dependency_revised", "ref": target,
                                   "current_ref": latest, "path": dep_path})
                if isinstance(obj, Artifact) and obj.kind == "legal_research":
                    stack.append((target, dep_path))
            except (ValueError, TypeError):
                issues.append({"code": "dependency_unavailable", "ref": target, "path": dep_path})
    stale_codes = {"object_revised", "dependency_changed", "dependency_revised", "dependency_unavailable",
                   "excerpt_binding_mismatch", "source_unavailable", "missing_research_object", "inspection_limit"}
    stale = any(issue["code"] in stale_codes for issue in issues)
    review = (reviews or {}).get(ref)
    valid_review = bool(review and review.get("fingerprint") == store.fingerprint(ref) and not stale)
    binding = ("invalid" if any(issue["code"] in {"excerpt_binding_mismatch", "source_unavailable",
                                                  "dependency_changed", "dependency_unavailable"} for issue in issues)
               else "incomplete" if any(issue["code"] in {"inspection_limit", "missing_research_object"} for issue in issues)
               else "intact")
    unresolved_codes = {"unverified_assumption", "unresolved_unknown", "unresolved_disputed", "support_gap", "inference_gap"}
    # Keep actionable flags before potentially long quotes/drafts so a model
    # preview cannot conceal an invalidated review behind the document body.
    return {"ref": ref, "current_ref": store.current_ref(ref),
            "stale": stale, "needs_review": not valid_review,
            "review_status": "user_reviewed" if valid_review else "review_invalidated" if review else "pending",
            "review": review if valid_review else None, "issues": issues,
            "unresolved_bases": [issue for issue in issues if issue["code"] in unresolved_codes],
            "mechanical_binding": binding,
            "legal_verification": False,
            "note": "Character matching and dependency checks are mechanical. Speaker labels, support relationships, "
                    "premise states, reasoning, scope and legal conclusions are assertions requiring legal review. "
                    "An assumption remains unverified, and a complete inference records reasoning without proving it. "
                    "User review records acknowledgement of this snapshot, not a correctness guarantee.",
            "assertions": assertions, "sources": sources, "object": data}


DIFF_CONTEXT = 240
DIFF_LIMIT = 200_000


def _text_diff(old: str, new: str) -> list[dict]:
    """Word-level changes with a little unchanged context; long equal runs are elided."""
    import difflib
    import re
    if len(old) > DIFF_LIMIT or len(new) > DIFF_LIMIT:
        return [{"op": "too_large", "old_chars": len(old), "new_chars": len(new)}]
    split = re.compile(r"\s+|\w+|[^\w\s]")
    old_tokens, new_tokens = split.findall(old), split.findall(new)
    old_at = [0]
    for token in old_tokens:
        old_at.append(old_at[-1] + len(token))
    new_at = [0]
    for token in new_tokens:
        new_at.append(new_at[-1] + len(token))
    codes = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False).get_opcodes()
    segments = []
    for index, (op, i1, i2, j1, j2) in enumerate(codes):
        if op != "equal":
            segments.append({"op": op, "old": "".join(old_tokens[i1:i2]), "new": "".join(new_tokens[j1:j2]),
                             "old_offset": old_at[i1], "new_offset": new_at[j1]})
            continue
        text = "".join(old_tokens[i1:i2])
        # Keep context only on the sides that touch a change.
        keep_head = text[:DIFF_CONTEXT] if index > 0 else ""
        keep_tail = text[-DIFF_CONTEXT:] if index < len(codes) - 1 else ""
        if len(codes) == 1 or len(text) <= len(keep_head) + len(keep_tail):
            segments.append({"op": "equal", "text": text})
        else:
            segments.append({"op": "equal", "text": keep_head, "elided": len(text) - len(keep_head) - len(keep_tail),
                             "tail": keep_tail})
    return segments


def compare(store, record_ref: str) -> dict:
    """What changed between a cited record version and its latest version.

    Read-only. Shows each changed text field, where every saved excerpt of the
    old version stands in the new one, and which research objects depend on
    it. It never rebinds anything: a revision is made by saving a new excerpt
    from the new version and revising each affected object explicitly.
    """
    old = store.get_exact(record_ref, Record)
    current_ref = store.current_ref(record_ref)
    new = store.get_exact(current_ref, Record)
    fields = []
    for name in sorted(set(old.fields) | set(new.fields)):
        before, after = old.fields.get(name), new.fields.get(name)
        old_text = before.value if before is not None and isinstance(before.value, str) else ""
        new_text = after.value if after is not None and isinstance(after.value, str) else ""
        if old_text != new_text:
            fields.append({"field": name, "old_chars": len(old_text), "new_chars": len(new_text),
                           "segments": _text_diff(old_text, new_text)})
    # Reverse dependency graph over every saved research object version.
    dependents, nodes = {}, {}
    for ref, obj in store.all(Artifact):
        if obj.kind != "legal_research":
            continue
        try:
            node = unpack(obj)
        except ValueError:
            continue
        nodes[ref] = node
        for dep in node.get("dependencies", []):
            dependents.setdefault(dep["ref"], []).append(ref)
    excerpts = []
    for ref in dependents.get(record_ref, []):
        node = nodes[ref]
        if node["type"] != "excerpt":
            continue
        source = node["source"]
        field = new.fields.get(source["field"])
        text = field.value if field is not None and isinstance(field.value, str) else ""
        hits, at = [], text.find(node["quote"])
        while at >= 0 and len(hits) < 3:
            hits.append(at)
            at = text.find(node["quote"], at + 1)
        status = ("missing" if not hits else "ambiguous" if len(hits) > 1
                  else "same_position" if hits[0] == source["start"] else "moved")
        excerpts.append({"ref": ref, "is_latest": store.current_ref(ref) == ref, "field": source["field"],
                         "quote": node["quote"], "old_start": source["start"], "new_status": status,
                         "new_start": hits[0] if len(hits) == 1 else None})
    affected, queue, seen = [], [(record_ref, 0)], {record_ref}
    while queue and len(affected) < 400:
        ref, depth = queue.pop(0)
        for parent in dependents.get(ref, []):
            if parent in seen:
                continue
            seen.add(parent)
            affected.append({"ref": parent, "type": nodes[parent]["type"], "depth": depth + 1, "via": ref,
                             "is_latest": store.current_ref(parent) == parent})
            queue.append((parent, depth + 1))
    return {"ref": record_ref, "current_ref": current_ref, "newer_version": current_ref != record_ref,
            "fields": fields, "excerpts": excerpts, "affected": affected,
            "note": "Text comparison is mechanical. Whether a change matters to an assertion needs review; "
                    "nothing has been rebound to the new version."}
