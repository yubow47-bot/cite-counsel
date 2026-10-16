"""Small opt-in real-model investigations with an explicit qualitative rubric.

This is a capability probe, not the scripted integration smoke suite. It
never reads user conversations and makes paid calls only with --live.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

TASKS = {
    "bill-status": {
        "prompt": "加拿大联邦语境下，我看到新闻说一项bill已经提出，想知道能不能据此认为相关规则现在已经生效？请实际调查官方说明，核查关键论证，给一个普通人能理解的答案并保留未决事项。可参考官方页面 https://www.ourcommons.ca/procedure/our-procedure/legislativeProcess/c_g_legislativeprocess-e.html 。这里没有给出具体法案号，也不要编造一项具体法案的当前状态。",
        "rubric": [
            "Actually obtains official material or explicitly reports retrieval failure; never invents a completed investigation.",
            "Distinguishes introduction, enactment/royal assent and coming into force, including a potentially later commencement date.",
            "Does not infer any particular bill's current status; identifies what is needed to check that status.",
            "Preserves the main question, executed investigation and at least one key argument assessment, with conditions and source links.",
        ],
    },
    "policy-causality": {
        "prompt": "我看到一个政策宣传，说新政策实行后事故从20起下降到10起，所以政策使事故减少了一半。以下只是虚构练习数据：同期未实施政策的对照地区事故从20起下降到5起，样本量和其他背景没有提供。我们能支持宣传里的因果结论吗？请调查如何区分相关与因果，并对关键论证做检查，给普通人清楚的答复。可查加拿大统计局官方解释 https://www.statcan.gc.ca/en/wtc/data-literacy/catalogue/892000062021002 。请区分计算结果、因果推断和仍需要的证据。",
        "rubric": [
            "Obtains the official methodological source or honestly reports failure; preserves supplied numbers as synthetic data.",
            "Computes the treated decline as 50% and the comparison decline as 75%, without inventing data or claiming statistical significance.",
            "Rejects a proven 50% causal effect from the before/after comparison; considers the stronger decline in the comparison region.",
            "Explains remaining causal assumptions/confounders and useful next evidence, while saving an inquiry and critical argument checks.",
        ],
    },
    "workplace-scope": {
        "prompt": "我在安省工作，看到加拿大政府的联邦劳动标准页面说解雇通常至少提前两周通知。是不是安省所有工作都可以直接套这个规则，而且做满三个月就一定只能拿两周？请实际查官方资料，核查适用范围、年资和例外，再给我一个可用的简短答复。我的行业、工龄、解雇原因和合同内容尚未提供。可从 https://www.canada.ca/en/services/jobs/workplace/federal-labour-standards/termination.html 和 https://www.canada.ca/en/services/jobs/workplace/federally-regulated-industries.html 开始。不要替我编造这些事实，也不要凭这两个网页直接计算我的全部法律权益。",
        "rubric": [
            "Actually retrieves official material addressing both termination rules and federal coverage.",
            "Does not apply federal labour standards automatically to every Ontario job; asks about the industry and relevant jurisdiction.",
            "Distinguishes a minimum from a fixed two-week entitlement; notes service-related increases and applicable exceptions, without computing unspecified individual entitlements.",
            "Delivers a concise useful answer with actual source links, saved key checks and the important missing facts; does not equate statutory minimums with all possible rights.",
        ],
    },
}


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def answer_status(answer, inspection=None):
    """Delivery, a matching saved answer and task quality are separate checks."""
    saved = (((inspection or {}).get("object") or {}).get("answer") or {}).get("text")
    delivered = isinstance(answer, str) and bool(answer.strip())
    return {"answer_delivered": delivered,
            "answer_saved": bool(delivered and isinstance(saved, str) and saved.strip() == answer.strip()),
            "quality_grade": "pending_independent_review"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly allow bounded real model and public-source calls.")
    parser.add_argument("--task", choices=["all", *TASKS], default="all")
    parser.add_argument("--model", default="")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default=None)
    parser.add_argument("--output", type=Path, default=Path(".chatbox-runtime/inquiry-eval"))
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("Use --live to run a bounded real-model capability probe; offline smoke is python -m eval.runner.")
    from core.chatbox_settings import configure
    from harness import core
    from harness.core import Harness
    from plugins.inquiry import PLUGIN as inquiry_plugin
    from plugins.research import PLUGIN as research_plugin
    from plugins.web import PLUGIN as web_plugin
    import llm_api.openrouter_api as provider

    settings = configure()
    saved = json.loads(core.CONFIG_PATH.read_text(encoding="utf-8")) if core.CONFIG_PATH.is_file() else {}
    model = args.model or saved.get("model") or settings["llm_model"]
    core.MAX_STEPS, core.MAX_TOOL_CALLS, core.MAX_TURN_SECONDS = 14, 36, 200
    original = provider.post_chat_completion

    def bounded(body, **kwargs):
        body = {**body, "max_tokens": body.get("max_tokens", 7000)}
        if args.reasoning_effort and "reasoning" in body and not body["reasoning"].get("effort"):
            body["reasoning"] = {"effort": args.reasoning_effort}
        return original(body, **{**kwargs, "read_timeout": min(kwargs.get("read_timeout", 60), 45)})

    provider.post_chat_completion = bounded
    logging.getLogger("harness.core").setLevel(logging.WARNING)
    root = args.output / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    root.mkdir(parents=True, exist_ok=True)
    names = list(TASKS) if args.task == "all" else [args.task]
    summaries = []
    for name in names:
        directory = root / name
        h = Harness({p.name: p for p in (inquiry_plugin, research_plugin, web_plugin)},
                    model=model, config_path=directory / "harness.json")
        # Direct public-page reading needs no search-provider subscription.
        # The isolated evaluation must actually offer its advertised source tool.
        h.set_enabled("web", True)
        session, _ = h.sessions.start()
        write(directory / "task.json", TASKS[name])
        write(directory / "config.json", {"model": model, "mode": "real-model-public-source", "synthetic_policy_numbers": True,
              "max_steps": core.MAX_STEPS, "max_calls": core.MAX_TOOL_CALLS, "max_seconds": core.MAX_TURN_SECONDS,
              "max_output_tokens": 7000, "reasoning_effort": args.reasoning_effort,
              "graded": False, "legal_capability_score": None})
        error = None
        started = time.monotonic()
        try:
            blocks = h.run_turn(session, TASKS[name]["prompt"])
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
            blocks = session.timeline[-1].get("blocks", [])
        active = session.state.get("inquiry", {}).get("active_ref")
        inspection = None
        if active:
            from core import inquiry
            inspection = inquiry.inspect(session.records, active, session.tool_receipts)
            write(directory / "inspection.json", inspection)
        answer = "\n\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not answer.strip() and error is None:
            error = "No complete answer was delivered; saved progress is not task completion."
        (directory / "answer.md").write_text(answer, encoding="utf-8")
        write(directory / "blocks.json", blocks)
        write(directory / "receipts.json", session.tool_receipts)
        event_path = h.sessions.store_dir / (session.id + ".events.jsonl")
        events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()] if event_path.exists() else []
        results = [row["payload"] for row in events if row.get("kind") == "tool_result"]
        summary = {"task": name, "model": model, "error": error, "active_inquiry": active,
                   "elapsed_seconds": round(time.monotonic() - started, 2),
                   "model_requests": sum(row.get("kind") == "model_request" for row in events),
                   "answer_review_requests": sum(row.get("kind") == "answer_review_request" for row in events),
                   "tool_errors": [{"name": row.get("name"), "error": row["content"]["error"]} for row in results
                                   if isinstance(row.get("content"), dict) and "error" in row["content"]],
                   "actual_source_calls": sum(row.get("status") == "ok" and row.get("category") in {"source", "extract", "read"}
                                              for row in session.tool_receipts),
                   "tool_calls": len(session.tool_receipts), **answer_status(answer, inspection),
                   "rubric": TASKS[name]["rubric"]}
        write(directory / "summary.json", summary)
        summaries.append(summary)
        print(json.dumps({"task": name, "directory": str(directory), "error": error, "tool_calls": len(session.tool_receipts)}, ensure_ascii=False), flush=True)
    write(root / "summary.json", {"mode": "real-model-public-source", "tasks": summaries, "graded": False})
    print(json.dumps({"report": str(root / "summary.json")}), flush=True)
    return 1 if any(task["error"] for task in summaries) else 0


if __name__ == "__main__":
    raise SystemExit(main())
