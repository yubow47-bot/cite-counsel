"""Lay a conversation's audit log out as per-turn timelines.

Read-only and derived from ``<session>.events.jsonl``. Logs written before
turn markers and budget snapshots existed still load; what they never
recorded is reported as None rather than guessed.
"""

from __future__ import annotations

import json
from pathlib import Path


def read_events(path: Path, limit: int = 20000) -> list[dict]:
    """Parse the log, skipping a torn last line from an interrupted write."""
    if not path.is_file():
        return []
    events = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and isinstance(event.get("kind"), str):
                events.append(event)
    return events[-limit:]


def _detail(kind: str, payload: dict, pending: dict, at: float) -> dict:
    if kind == "turn_start":
        return {"text": payload.get("text")}
    if kind == "model_request":
        return {"model": payload.get("model"), "messages": len(payload.get("messages") or []),
                "tools": len(payload.get("tools") or [])}
    if kind == "model_response":
        calls = [((call.get("function") or {}).get("name")) for call in payload.get("tool_calls") or []
                 if isinstance(call, dict)]
        return {"tool_calls": calls} if calls else {"reply_chars": len(payload.get("content") or "")}
    if kind == "tool_call":
        pending[payload.get("id")] = at
        return {"tool": (payload.get("function") or {}).get("name"), "call_id": payload.get("id")}
    if kind == "tool_result":
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        receipt = content.get("_execution_receipt") if isinstance(content.get("_execution_receipt"), dict) else {}
        started = pending.pop(payload.get("call_id"), None)
        return {"tool": payload.get("name"), "call_id": payload.get("call_id"),
                "error": content.get("error"), "receipt_status": receipt.get("status"),
                "receipt_id": receipt.get("id"),
                "seconds": round(at - started, 2) if started is not None else None}
    if kind in {"answer_review_request", "answer_review_response"}:
        return {"model": payload.get("model"), "mode": payload.get("mode")}
    if kind == "answer_review_result":
        return {"status": payload.get("status"), "reason": payload.get("reason"), "model": payload.get("model")}
    if kind in {"model_interrupted", "answer_unavailable"}:
        return {"reason": payload.get("reason")}
    if kind == "answer_delivered":
        return {"chars": len(payload.get("text") or "")}
    return {}


def summarize(events: list[dict], *, turns: int = 5, rows: int = 300) -> list[dict]:
    """The last ``turns`` turns, each with time offsets from its start."""
    result, current, pending = [], None, {}
    for event in events:
        kind, at = event["kind"], event.get("time")
        if not isinstance(at, (int, float)):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        state = event.get("state") if isinstance(event.get("state"), dict) else {}
        if kind == "turn_start" or current is None:
            current = {"started": at, "marked": kind == "turn_start", "text": None, "rows": [], "truncated": False}
            result.append(current)
            pending = {}
        detail = _detail(kind, payload, pending, at)
        if kind == "turn_start":
            current["text"] = detail.get("text")
            continue
        if len(current["rows"]) >= rows:
            current["truncated"] = True
            continue
        current["rows"].append({"at": round(at - current["started"], 2), "kind": kind,
                                "phase": state.get("phase"), "remaining_s": state.get("remaining_s"),
                                "tool_calls": state.get("tool_calls"), "max_calls": state.get("max_calls"),
                                **detail})
    for turn in result:
        turn["seconds"] = turn["rows"][-1]["at"] if turn["rows"] else 0
    return result[-turns:]
