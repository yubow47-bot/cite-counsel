"""Short sourced completion still enforces source, receipt and revision gates."""
import json

import pytest

from core import inquiry, research
from core.tool_contracts import Field, Record
from harness import core, llm
from harness.runtime import TurnBudget
from harness.core import Harness
from harness.plugin import discover
from plugins import inquiry as plugin


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    h = Harness(discover(), model="offline", config_path=tmp_path / "h.json")
    session, _ = h.sessions.start()
    session.messages.append({"role": "user", "content": "Does sending the notice establish receipt?"})
    session.timeline.append({"role": "user", "text": "Does sending the notice establish receipt?"})
    source = session.records.put(Record("document", {"text": Field(
        "Paper notice is effective on receipt. Dispatch does not establish receipt.", "extracted")}, "fixture", "notice"))
    h._execute_once(session, {"function": {"name": "record__read", "arguments": json.dumps({"ref": source, "field": "text"})}}, {})
    # These integration tests exercise delivery after a successful exact-text
    # review. Review failure and semantic edits have separate explicit tests.
    monkeypatch.setattr(plugin, "review_candidate", lambda ctx, p, contexts, delivery_evidence=None: (
        p, {"status": "model_reviewed", "reviewed_answer_sha256": research.digest(p.answer), "semantic_verification": False}))
    return h.context(session, "inquiry")


def params(**changes):
    values = {"calculations": [], "answer": "Dispatch alone is insufficient; establish actual receipt.", "jurisdiction": "Fixture",
              "checks": [{"claim_kind": "direct", "conclusion": "Dispatch does not establish receipt.",
                          "passages": [{"source_ref": "rec_1", "quote": "Dispatch does not establish receipt."}],
                          "reasoning": "The retrieved rule expressly distinguishes the two events.",
                          "objection": "A dispatch-based exception could change this answer.",
                          "response": "The retrieved passage contains no such exception; broader exceptions remain untested."}]}
    values.update(changes)
    return plugin.ConcludeParams.model_validate(values)


def test_one_call_preserves_evidence_checks_and_the_delivered_answer(ctx):
    result = plugin.conclude(ctx, params())
    report = inquiry.inspect(ctx.records, result.content["ref"], ctx.session.tool_receipts)
    assert result.reply == report["object"]["answer"]["text"]
    assert result.content["answer_delivered"] and ctx.session.retained
    assert report["tasks"][0]["execution"] == "material_returned"
    assessment = report["assessments"][0]
    assert assessment["receipts"][0]["id"] == ctx.session.tool_receipts[0]["id"]
    assert assessment["response"].startswith("The retrieved passage")
    claim = research.inspect(ctx.records, assessment["claim_ref"])
    assert claim["object"]["counterargument"]
    assert claim["mechanical_binding"] == "intact"
    assert not assessment["semantic_verification"]
    assert "Dispatch does not establish receipt." in plugin.export_action(ctx, {}).blocks[0]["markdown"]


@pytest.mark.parametrize("failure", ["invented_quote", "no_receipt", "wrong_kind", "unknown_task", "late_bad_passage"])
def test_invalid_compound_inputs_do_not_leave_half_an_inquiry(ctx, failure):
    p = params()
    if failure == "invented_quote":
        p.checks[0].passages[0].quote = "An invented quote."
    elif failure == "no_receipt":
        ctx.session.tool_receipts.clear()
    elif failure == "wrong_kind":
        ref = plugin.frame(ctx, plugin.FrameParams(question="Existing question")).content["ref"]
        p.checks[0].passages[0].source_ref = ref
    elif failure == "unknown_task":
        p.checks[0].task_ids = ["q999"]
    else:
        p.checks.append(p.checks[0].model_copy(deep=True))
        p.checks[1].passages[0].quote = "An invented late quote."
    before = list(ctx.records.all())
    with pytest.raises(ValueError):
        plugin.conclude(ctx, p)
    assert list(ctx.records.all()) == before


def test_compound_completion_does_not_close_unaddressed_subquestions(ctx):
    ref = plugin.frame(ctx, plugin.FrameParams(question="Notice question", questions=[
        {"id": "receipt", "question": "What is required?"}, {"id": "exception", "question": "Which exception applies?"}])).content["ref"]
    p = params(ref=ref, gaps=["The applicable exception is unknown."])
    p.checks[0].task_ids = ["receipt"]
    result = plugin.conclude(ctx, p)
    report = inquiry.inspect(ctx.records, result.content["ref"], ctx.session.tool_receipts)
    assert [task["status"] for task in report["tasks"]] == ["resolved", "open"]
    assert report["readiness"] == "open"
    assert report["object"]["answer"]["gaps"]


def test_omitting_ref_on_a_new_turn_does_not_overwrite_an_unrelated_question(ctx):
    old = plugin.frame(ctx, plugin.FrameParams(question="Another matter")).content["ref"]
    ctx.session.timeline.append({"role": "user", "text": "A new matter"})
    result = plugin.conclude(ctx, params())
    assert ctx.records.current_ref(old) == old
    assert result.content["ref"] != old


def test_explicit_completion_delivers_without_a_further_model_round(ctx, monkeypatch):
    h, session = ctx._harness, ctx.session
    session.loaded.append("inquiry")
    calls = []

    def stream(*args, **kwargs):
        calls.append(1)
        assert len(calls) == 1, "A saved final answer should not need another model response"
        yield "done", {"tool_calls": [{"id": "finish", "function": {
            "name": "inquiry__conclude", "arguments": params().model_dump_json(exclude_unset=True)}}]}

    monkeypatch.setattr(llm, "chat_stream", stream)
    blocks = h.run_turn(session, "Does dispatch alone prove receipt?")
    assert [block["text"] for block in blocks if block["type"] == "text"] == [params().answer]
    assert session.messages[-1]["content"] == params().answer


def test_long_model_stream_is_closed_in_time_to_produce_an_answer(ctx, monkeypatch):
    elapsed, requests, closed = [0.0], [], []
    monkeypatch.setattr(core.time, "monotonic", lambda: elapsed[0])

    def stream(model, messages, tools, **kwargs):
        requests.append(tools)
        if len(requests) == 1:
            try:
                elapsed[0] = core.MAX_TURN_SECONDS - 10
                yield "reasoning", "Unfinished deliberation"
                pytest.fail("The unfinished model stream must be closed")
            finally:
                closed.append(True)
        else:
            assert not tools
            yield "done", {"content": "Useful conditional answer from the completed evidence."}

    monkeypatch.setattr(llm, "chat_stream", stream)
    blocks = ctx._harness.run_turn(ctx.session, "Investigate")
    assert closed and len(requests) == 2
    assert any(block.get("text", "").startswith("Useful conditional") for block in blocks)


def test_tool_calls_request_bounded_reasoning_without_disabling_it(monkeypatch):
    import llm_api.openrouter_api as provider
    bodies = []
    monkeypatch.setattr(provider, "post_chat_completion", lambda body, **kwargs: bodies.append(body))
    llm._post("fixture", [], [{"type": "function"}], 10, include_reasoning=True)
    assert bodies[0]["reasoning"] == {"enabled": True, "effort": "low"}


def test_final_answer_includes_actual_source_links_even_if_model_omits_them(ctx):
    source = ctx.records.put(Record("document", {"text": Field("An exact rule.", "extracted"),
                                "url": Field("https://example.org/official-rule", "extracted")}, "fixture", "rule"))
    ctx._harness._execute_once(ctx.session, {"function": {"name": "record__read", "arguments": json.dumps({"ref": source, "field": "text"})}}, {})
    p = params()
    p.checks[0].passages = [plugin.Passage(source_ref=source, quote="An exact rule.")]
    result = plugin.conclude(ctx, p)
    assert "https://example.org/official-rule" in result.reply
    report = inquiry.inspect(ctx.records, result.content["ref"], ctx.session.tool_receipts)
    assert report["object"]["answer"]["text"] == result.reply


def test_fresh_context_review_changes_both_answer_and_critical_check(ctx, monkeypatch):
    import time
    from plugins.inquiry.review import review_candidate
    ctx.session.running = True
    ctx.session.runtime = TurnBudget(time.monotonic() + 90, 18, 60, 0)
    p = params()
    revised = {"answer": "Not established: the available material does not prove the exception.",
               "checks": [{**{key: getattr(p.checks[0], key) for key in ("conclusion", "reasoning", "objection", "response", "verdict")},
                           "conclusion": "The exception has not been established.", "verdict": "limited"}],
               "corrections": ["Removed an unsupported categorical statement."]}

    def review(model, messages, tools, **kwargs):
        assert tools == [] and kwargs["timeout"] <= 40 and kwargs["reasoning_effort"] == "medium"
        payload = json.loads(messages[1]["content"])
        assert payload["task"]["latest_user_message"] and payload["source_contexts"]
        assert payload["candidate"] == p.answer and len(payload["checks"]) == len(p.checks)
        return {"content": json.dumps(revised)}

    monkeypatch.setattr(llm, "chat", review)
    new, audit = review_candidate(ctx, p, [{"text": "Exact surrounding rule"}])
    assert new.answer.startswith("Not established") and new.checks[0].conclusion.startswith("The exception")
    assert new.checks[0].passages == p.checks[0].passages
    assert audit["status"] == "model_reviewed" and not audit["semantic_verification"]


@pytest.mark.parametrize("bad", ["malformed", "missing_check", "timeout"])
def test_review_failure_preserves_answer_and_does_not_retry(ctx, monkeypatch, bad):
    import time
    from plugins.inquiry.review import review_candidate
    ctx.session.running = True
    ctx.session.runtime = TurnBudget(time.monotonic() + 90, 18, 60, 0)
    calls = []

    def review(*args, **kwargs):
        calls.append(1)
        if bad == "timeout":
            raise llm.LLMError("Unavailable")
        return {"content": "broken" if bad == "malformed" else '{"answer":"Changed", "checks":[], "corrections":[]}'}

    monkeypatch.setattr(llm, "chat", review)
    p = params()
    new, audit = review_candidate(ctx, p, [])
    assert new is p and audit["status"] == "failed" and len(calls) == 1


def test_unconfigured_search_is_not_offered_but_direct_fetch_still_is(ctx):
    h, session = ctx._harness, ctx.session
    h.set_enabled("web", True)
    session.loaded.append("web")
    names = {spec["function"]["name"] for spec in h._tool_specs(session)}
    assert "web__fetch" in names and "web__search" not in names
    _, result = h._execute(session, {"function": {"name": "web__search", "arguments": '{"query":"official rule"}'}})
    assert result.content["kind"] == "unavailable_tool"
    h.set_plugin_settings("web", {"provider": "duckduckgo_lite"})
    assert "web__search" in {spec["function"]["name"] for spec in h._tool_specs(session)}


def test_verbose_receipts_do_not_hide_decisive_passage_offsets(ctx):
    from harness.core import _model_content
    matches = [{"ref": "rec_1", "field": "text", "start": index * 2000, "text": "Original source context. " * 8} for index in range(8)]
    receipt = {"id": "server-issued", "tool": "record__search", "category": "read", "status": "ok",
               "observations": [{"text_sha256": "x" * 64, "offset": i, "extra": "long audit data " * 20} for i in range(8)]}
    preview = json.loads(_model_content({"matches": matches, "_execution_receipt": receipt}))
    assert preview["matches"][-1]["start"] == 14000
    assert preview["_execution_receipt"]["id"] == "server-issued"
    assert "observations" not in preview["_execution_receipt"]
    assert len(receipt["observations"]) == 8  # input / durable ledger unchanged


def test_repeated_read_keeps_original_text_but_explains_how_to_make_progress(ctx):
    _, result = ctx._harness._execute_once(ctx.session, {"function": {"name": "record__read", "arguments": '{"ref":"rec_1","field":"text"}'}}, {})
    assert result.content["text"].startswith("Paper notice") and "repeat_note" in result.content
    assert ctx.session.tool_receipts[-1]["executed"]


def test_invalid_ellipsis_quote_returns_original_repair_context_without_accepting_it(ctx):
    ctx.session.loaded.append("inquiry")
    p = params()
    p.checks[0].passages[0].quote = "Paper notice is effective...Dispatch does not establish receipt."
    before = list(ctx.records.all())
    _, result = ctx._harness._execute(ctx.session, {"function": {"name": "inquiry__conclude", "arguments": p.model_dump_json(exclude_unset=True)}})
    assert result.content["error"] and result.content["repair"]["source_ref"] == "rec_1"
    assert "Paper notice is effective on receipt." in result.content["repair"]["text"]
    assert list(ctx.records.all()) == before


def test_plain_answer_after_source_read_gets_one_saved_completion_not_another_planning_loop(ctx, monkeypatch):
    stream_calls, reviews = [], []

    def stream(*args, **kwargs):
        stream_calls.append(1)
        if len(stream_calls) == 1:
            yield "done", {"tool_calls": [{"id": "read", "function": {"name": "record__read", "arguments": '{"ref":"rec_1","field":"text"}'}}]}
        else:
            yield "done", {"content": "Provisional unsupported generalization. " * 5}

    def review(model, messages, tools, **kwargs):
        reviews.append(1)
        assert kwargs["tool_choice"]["function"]["name"] == "inquiry__conclude"
        payload = json.loads(messages[1]["content"])
        assert payload["source_contexts"][0]["text"].startswith("Paper notice")
        return {"tool_calls": [{"id": "complete", "function": {"name": "inquiry__conclude", "arguments": params().model_dump_json(exclude_unset=True)}}]}

    monkeypatch.setattr(llm, "chat_stream", stream)
    monkeypatch.setattr(llm, "chat", review)
    blocks = ctx._harness.run_turn(ctx.session, "Investigate receipt and check the key reasoning.")
    assert len(stream_calls) == 2 and len(reviews) == 1
    assert blocks[-1]["text"] == params().answer
    assert ctx.session.messages[-1]["content"] == params().answer
    report = plugin.inspect_object(ctx, plugin.InspectParams()).content
    assert report["assessments"] and report["object"]["answer"]["review"]["mode"] == "final_completion"


def test_final_completion_failure_retains_attributed_evidence_instead_of_unreviewed_draft(ctx, monkeypatch):
    monkeypatch.setattr(llm, "chat_stream", lambda *a, **kwargs: iter([("done", {"content": "A useful provisional answer. " * 6})]))
    ctx.session.runtime = TurnBudget(__import__("time").monotonic() + 60, 12, 60, 0)
    ctx.session.running = True
    monkeypatch.setattr(llm, "chat", lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("Unavailable")))
    blocks = []
    draft = "A useful provisional answer. " * 6
    result = ctx._harness._complete_reply(ctx.session, draft, blocks, {})
    assert result != draft and "review did not complete" in result
    assert "The source states (rec_1)" in result
    assert "> Paper notice is effective on receipt. Dispatch does not establish receipt." in result
    assert "A useful provisional answer" not in result and blocks[0]["type"] == "notice"
    assert "provisional" in blocks[0]["text"]
    assert ctx.session.runtime.reviewed_call_id is None


def test_rendering_never_calls_model_or_completion_provider(ctx, monkeypatch):
    from dataclasses import replace
    h = ctx._harness
    def forbidden(*args, **kwargs):
        pytest.fail("Rendering must not run completion work")
    h.plugins["inquiry"] = replace(h.plugins["inquiry"], prepare_completion=forbidden)
    monkeypatch.setattr(llm, "chat", forbidden)
    blocks = []
    h._add_caption(ctx.session, "The already delivered answer", blocks)
    assert blocks[-1]["text"] == "The already delivered answer"


def test_saved_answer_replay_is_exact_but_changed_prose_requires_fresh_review(ctx):
    result = plugin.conclude(ctx, params())
    replay = plugin.answer(ctx, plugin.AnswerParams(ref=result.content["ref"], text=result.reply))
    assert replay.reply == result.reply and replay.content["ref"] == result.content["ref"]
    another = plugin.answer(ctx, plugin.AnswerParams(ref=result.content["ref"], text="Receipt remains unproved."))
    assert another.reply != "Receipt remains unproved." and another.final
    assert "review did not complete" in another.reply
    bound = plugin.final_response_checks(ctx, another.reply)[0]
    assert bound["answer_matches"] and len(bound["answer_sha256"]) == 64
    mismatch = plugin.final_response_checks(ctx, "Receipt has definitely been proved.")[0]
    assert not mismatch["answer_matches"] and mismatch["readiness"] == "open"
    assert mismatch["items"][0]["code"] == "answer_mismatch"


def test_completion_uses_shared_call_budget_and_records_recoverable_tool_history(ctx, monkeypatch):
    import time
    h, session = ctx._harness, ctx.session
    session.running = True
    session.runtime = TurnBudget(time.monotonic() + 90, 18, 1, 0)
    draft = "Provisional explanation requiring the saved decisive checks. " * 3
    request = {"tool_calls": [{"id": "external-id", "function": {"name": "inquiry__conclude", "arguments": params().model_dump_json(exclude_unset=True)}}]}
    monkeypatch.setattr(llm, "chat", lambda *a, **k: request)
    blocks = []
    h._deliver_reply(session, draft, blocks, {})
    assert session.runtime.tool_count == 1
    assert session.runtime.review_attempted and session.runtime.reviewed_call_id is None
    assert blocks[-1]["text"] == params().answer
    calls = [c for m in h._history(session) for c in m.get("tool_calls", [])]
    results = [m for m in h._history(session) if m["role"] == "tool"]
    assert len(calls) == len(results) == 1 and calls[0]["id"] == results[0]["tool_call_id"]
    assert session.messages[-1]["content"] == params().answer
    # Persisted history contains the same exchange; ephemeral budget is absent.
    h.sessions.save(session)
    archived = json.loads((h.sessions.store_dir / (session.id + ".json")).read_text(encoding="utf-8"))
    assert "runtime" not in archived and archived["messages"][-2]["role"] == "tool"


def test_completion_cannot_bypass_exhausted_tool_budget(ctx, monkeypatch):
    import time
    ctx.session.running = True
    ctx.session.runtime = TurnBudget(time.monotonic() + 90, 18, 1, 0, tool_count=1)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: pytest.fail("No calls left"))
    before = len(ctx.session.tool_receipts)
    blocks = []
    draft = "Useful provisional explanation. " * 6
    result = ctx._harness._complete_reply(ctx.session, draft, blocks, {})
    assert "review did not complete" in result and "The source states (rec_1)" in result
    assert "Useful provisional explanation" not in result
    assert blocks[0]["type"] == "notice" and len(ctx.session.tool_receipts) == before


def test_review_cancellation_is_propagated_without_saving_a_candidate(ctx, monkeypatch):
    import time
    from plugins.inquiry.review import review_candidate
    ctx.session.running = True
    ctx.session.runtime = TurnBudget(time.monotonic() + 90, 18, 60, 0)
    monkeypatch.setattr(plugin, "review_candidate", review_candidate)
    def cancel(*args, **kwargs):
        ctx.session.cancel()
        raise llm.LLMCancelled("Stopped")
    monkeypatch.setattr(llm, "chat", cancel)
    before = list(ctx.records.all())
    with pytest.raises(llm.LLMCancelled):
        plugin.conclude(ctx, params())
    assert list(ctx.records.all()) == before


def test_task_context_preserves_earlier_givens_and_relevant_saved_scope(ctx):
    from plugins.inquiry.review import task_context
    ctx.session.messages.append({"role": "user", "content": "The notice was sent before the deadline; receipt time is unknown."})
    ref = plugin.frame(ctx, plugin.FrameParams(question="When is notice effective?", scope={"jurisdiction": "Fixture", "factual_context": "Dispatch before the deadline is given; receipt is unknown."})).content["ref"]
    ctx.session.messages.append({"role": "user", "content": "Continue with that assumption."})
    data = task_context(ctx, params(ref=ref))
    assert "sent before" in data["earlier_user_messages"][-1]["text"]
    assert data["saved_inquiry"]["scope"]["jurisdiction"] == "Fixture"
    assert data["latest_user_message"] == "Continue with that assumption."


def test_optional_review_has_one_attempt_per_turn(ctx, monkeypatch):
    import time
    ctx.session.running = True
    ctx.session.runtime = TurnBudget(time.monotonic() + 90, 18, 60, 0)
    calls = []
    def model(*args, **kwargs):
        calls.append(kwargs)
        return {"content": "A complete response"}
    monkeypatch.setattr(llm, "chat", model)
    first, _ = ctx.request_review([])
    second, audit = ctx.request_review([])
    assert first and second is None and len(calls) == 1
    assert audit["reason"] == "review_already_attempted"
    assert calls[0]["deadline"] <= ctx.session.runtime.deadline - 8
    assert callable(calls[0]["cancelled"])


def test_tool_batch_stops_at_answer_reserve_and_closes_unexecuted_calls(ctx, monkeypatch):
    elapsed, executions, requests = [0.0], [], []
    monkeypatch.setattr(core.time, "monotonic", lambda: elapsed[0])
    original = ctx._harness._execute_once
    def execute(session, call, failed):
        executions.append(call["id"])
        result = original(session, call, failed)
        elapsed[0] = core.MAX_TURN_SECONDS - 10
        return result
    def stream(model, messages, tools, **kwargs):
        requests.append(tools)
        if len(requests) == 1:
            yield "done", {"tool_calls": [
                {"id": "first", "function": {"name": "record__read", "arguments": '{"ref":"rec_1","field":"text"}'}},
                {"id": "second", "function": {"name": "record__list", "arguments": '{}'}}]}
        else:
            assert not tools
            yield "done", {"content": "A conditional answer from the completed source read."}
    monkeypatch.setattr(ctx._harness, "_execute_once", execute)
    monkeypatch.setattr(llm, "chat_stream", stream)
    blocks = ctx._harness.run_turn(ctx.session, "Investigate")
    assert executions == ["first"] and len(requests) == 2
    second = next(m for m in ctx.session.messages if m.get("tool_call_id") == "second")
    assert json.loads(second["content"])["kind"] == "resource_limit"
    assert blocks[-1]["text"].startswith("A conditional answer")


def test_empty_model_response_is_reported_as_incomplete_not_successful_delivery(ctx, monkeypatch):
    monkeypatch.setattr(llm, "chat_stream", lambda *a, **k: iter([("done", {"content": "<think>Still thinking</think>"})]))
    blocks = ctx._harness.run_turn(ctx.session, "Investigate")
    assert any(b["type"] == "notice" and "complete answer" in b["text"] for b in blocks)
    events = [json.loads(line) for line in (ctx._harness.sessions.store_dir / (ctx.session.id + ".events.jsonl")).read_text(encoding="utf-8").splitlines()]
    assert any(e["kind"] == "answer_unavailable" for e in events)
    assert not any(e["kind"] == "answer_delivered" for e in events)
    assert ctx.session.runtime is None


def test_final_model_timeout_reports_incomplete_without_false_delivery(ctx, monkeypatch):
    calls = []
    def stream(*args, **kwargs):
        calls.append(kwargs)
        raise llm.LLMDeadline("No completed response")
        yield
    monkeypatch.setattr(llm, "chat_stream", stream)
    blocks = ctx._harness.run_turn(ctx.session, "Investigate")
    assert len(calls) == 2
    assert calls[0]["deadline"] < calls[1]["deadline"]
    assert blocks[-1]["type"] == "notice" and "complete answer" in blocks[-1]["text"]
    assert ctx.session.runtime is None



def _premise(n):
    return {"statement": f"Condition {n} holds.", "if_true": f"Answer A{n}.", "if_false": f"Answer B{n}.",
            "next_check": f"Check source {n}."}


def _review_reply(monkeypatch, body):
    monkeypatch.setattr(llm, "chat", lambda *a, **k: {"content": json.dumps(body)})


def _running(ctx):
    import time
    ctx.session.running = True
    ctx.session.runtime = TurnBudget(time.monotonic() + 90, 18, 60, 0)


def test_review_adding_premises_past_capacity_keeps_them_as_gaps(ctx, monkeypatch):
    from plugins.inquiry.review import review_candidate
    _running(ctx)
    p = params(checks=[{**params().checks[0].model_dump(), "claim_kind": "inference",
                        "premises": [_premise(1), _premise(2)]}])
    check = {key: getattr(p.checks[0], key) for key in ("conclusion", "reasoning", "objection", "response", "verdict")}
    _review_reply(monkeypatch, {"answer": "Reviewed answer.", "corrections": ["Added conditions."],
                                "checks": [{**check, "premises": [_premise(3), _premise(4)]}]})
    new, audit = review_candidate(ctx, p, [])
    assert audit["status"] == "model_reviewed" and new.answer == "Reviewed answer."
    assert [value.statement for value in new.checks[0].premises] == ["Condition 1 holds.", "Condition 2 holds.", "Condition 3 holds."]
    assert any("Condition 4 holds." in gap for gap in new.gaps)


def test_review_answering_under_the_input_gap_name_is_accepted(ctx, monkeypatch):
    from plugins.inquiry.review import review_candidate
    _running(ctx)
    p = params(gaps=["Original gap."])
    check = {key: getattr(p.checks[0], key) for key in ("conclusion", "reasoning", "objection", "response", "verdict")}
    _review_reply(monkeypatch, {"answer": "Reviewed answer.", "checks": [check], "known_gaps": ["Original gap.", "New gap."]})
    new, audit = review_candidate(ctx, p, [])
    assert audit["status"] == "model_reviewed" and new.gaps == ["Original gap.", "New gap."]
