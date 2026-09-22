"""The planner decides the move and writes the prose; the server owns the facts.

JEV and the chat model are both stubbed: no provider is called.
"""

import json

import pytest

from core import chatbox_service as service
from core import conversation
from core.decisions import jev_client
from core.decisions.models import DecisionResult


@pytest.fixture(autouse=True)
def isolated_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_ROUTE_LOG", tmp_path / "routes.jsonl")
    monkeypatch.setattr(conversation, "_ACTION_LOG", tmp_path / "actions.jsonl")
    monkeypatch.setattr(service, "item_store", service.ItemStore())
    monkeypatch.setattr(service, "candidate_store", service.CandidateStore())


def use_jev(monkeypatch, action, confidence=0.9):
    probabilities = {key: 0.0 for key in conversation.ACTIONS}
    probabilities[action] = 1.0

    class Stub:
        def __init__(self, *a, **k):
            pass

        def decide_choice(self, state, question):
            return DecisionResult(question.question_id, action, probabilities, confidence, "jev-latest", 90.0)

        def close(self):
            pass

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(jev_client, "JevClient", Stub)


def use_model(monkeypatch, answer):
    import llm_api.deepseek_api as deepseek
    prompts = []

    def ask(prompt, **kwargs):
        prompts.append(prompt)
        return json.dumps(answer)

    monkeypatch.setattr(deepseek, "ask_deepseek", ask)
    return prompts


def book_context(fields=None):
    """A book that still needs place, publisher and year."""
    fields = fields or {"title": {"value": "The Early Upanisads", "origin": "extracted"},
                        "author": {"value": "Patrick Olivelle", "origin": "extracted"}}
    item_id, token, item = service.item_store.add(source_type="book", fields=fields)
    return service.turn_context(item_id, token)


def candidate_context():
    records = [{"style_of_cause": "Edwards v Canada (AG)", "reporter": "[1930] AC 124", "verified": True},
               {"style_of_cause": "Edwards v Canada (AG)", "neutral_citation": "1929 CanLII 1", "verified": True}]
    block = service.candidate_store.add(records)
    return service.turn_context(candidate_set_id=block["candidate_set_id"],
                                candidate_token=block["access_token"])


# ── The fact guard on the model's free text ───────────────────────────


@pytest.mark.parametrize("message, grounded, kept", [
    ("出版年份还差一项，请照版权页填写。", "", True),
    ("这本书是 1998 年出版的，对吗？", "", False),
    ("你说的 1998 年，是版权页上的年份吗？", "用户说 1998 年", True),
    ("这条应该是 2002 SCC 10。", "", False),
    ("要引用 at para 64 吗？", "", False),
    ("你写的 at para 64 是这一段吗？", "改成 at para 64", True),
])
def test_message_is_kept_only_when_its_facts_are_already_visible(message, grounded, kept):
    assert bool(conversation.safe_message(message, grounded)) is kept


def test_message_that_is_too_long_or_not_text_is_dropped():
    assert conversation.safe_message("好" * 301, "") == ""
    assert conversation.safe_message(None, "") == ""


# ── provide_fields: values come out of the user's own words ───────────


def test_values_are_sliced_out_of_the_user_message(monkeypatch):
    use_jev(monkeypatch, "provide_fields")
    use_model(monkeypatch, {"values": [{"field": "year", "text": "1998"},
                                       {"field": "place", "text": "New York"},
                                       {"field": "publisher", "text": "Oxford University Press"}],
                            "message": "已按你说的填好，请核对。"})
    context = book_context()
    blocks = service.planned_blocks("New York 的 Oxford University Press，1998 年", context)
    result = next(b for b in blocks if b["type"] == "citation_result")
    assert result["citation"] == "Patrick Olivelle, *The Early Upanisads* (New York: Oxford University Press, 1998)."
    assert result["verified"] is False
    origins = {f["name"]: f["origin"] for f in result["fields"] if f["value"]}
    assert origins == {"author": "extracted", "title": "extracted",
                       "place": "user", "publisher": "user", "year": "user"}


def test_a_value_the_model_invented_is_refused_and_the_turn_asks_instead(monkeypatch):
    use_jev(monkeypatch, "provide_fields")
    # The user never wrote any of these; the model produced them from nowhere.
    use_model(monkeypatch, {"values": [{"field": "year", "text": "1998"},
                                       {"field": "place", "text": "New York"}],
                            "message": "已填好。"})
    blocks = service.planned_blocks("出版信息在版权页上", book_context())
    assert "citation_result" not in [b["type"] for b in blocks]
    question = next(b for b in blocks if b["type"] == "field_question")
    assert "1998" not in str(blocks) and "New York" not in str(blocks)
    assert {f["name"] for f in question["fields"]} >= {"place", "publisher", "year"}


def test_a_field_outside_the_template_is_ignored(monkeypatch):
    use_jev(monkeypatch, "provide_fields")
    use_model(monkeypatch, {"values": [{"field": "made_up", "text": "1998"},
                                       {"field": "year", "text": "1998"}],
                            "message": ""})
    plan = conversation.plan_turn("出版于 1998 年", book_context())
    assert plan.values == {"year": "1998"}


def test_a_message_may_repeat_a_year_the_user_wrote(monkeypatch):
    use_jev(monkeypatch, "provide_fields")
    use_model(monkeypatch, {"values": [{"field": "year", "text": "1998"}],
                            "message": "这是 Oxford 1998 年那一版，对吗？"})
    plan = conversation.plan_turn("出版于 1998 年", book_context())
    assert plan.values == {"year": "1998"}
    assert plan.message == "这是 Oxford 1998 年那一版，对吗？"


def test_a_message_inventing_a_citation_number_is_dropped(monkeypatch):
    use_jev(monkeypatch, "correct_fields")
    use_model(monkeypatch, {"fields": ["year"], "message": "你是说 [1930] AC 124 那一条吗？"})
    blocks = service.planned_blocks("年份不对", book_context())
    assert "[1930] AC 124" not in str(blocks)
    question = next(b for b in blocks if b["type"] == "field_question")
    assert [f["name"] for f in question["fields"]] == ["year"]


# ── correct_fields, select_candidate, change_type ─────────────────────


def test_correct_fields_asks_only_about_the_named_field(monkeypatch):
    use_jev(monkeypatch, "correct_fields")
    use_model(monkeypatch, {"fields": ["author"], "message": "作者要改成什么？"})
    blocks = service.planned_blocks("作者不对", book_context())
    question = next(b for b in blocks if b["type"] == "field_question")
    assert [f["name"] for f in question["fields"]] == ["author"]
    assert question["fields"][0]["value"] == "Patrick Olivelle"


def test_unnamed_fields_fall_back_to_the_whole_template(monkeypatch):
    use_jev(monkeypatch, "correct_fields")
    use_model(monkeypatch, {"fields": ["not_a_field"], "message": ""})
    plan = conversation.plan_turn("有个地方不对", book_context())
    assert [name for name in plan.fields] == ["author", "title", "edition", "place",
                                              "publisher", "year", "pinpoint"]


def test_the_second_one_resolves_to_a_signed_candidate(monkeypatch):
    use_jev(monkeypatch, "select_candidate")
    context = candidate_context()
    second = context.candidates[1]["id"]
    use_model(monkeypatch, {"candidate_id": second, "message": "好，用第二条。"})
    blocks = service.planned_blocks("要第二个", context)
    result = next(b for b in blocks if b["type"] == "citation_result")
    assert "1929 CanLII 1" in result["citation"] and result["verified"] is True


def test_a_candidate_id_outside_the_listing_is_refused(monkeypatch):
    use_jev(monkeypatch, "select_candidate")
    use_model(monkeypatch, {"candidate_id": "forged-id", "message": "好。"})
    # No plan means the turn falls through to the ordinary query pipeline.
    assert service.planned_blocks("要第二个", candidate_context()) is None


def test_change_type_carries_fields_over_and_keeps_each_origin(monkeypatch):
    use_jev(monkeypatch, "change_type")
    use_model(monkeypatch, {"source_type": "book", "message": "好，按书来处理。"})
    fields = {"author": {"value": "Dwight Newman", "origin": "extracted"},
              "title": {"value": "The Duty to Consult", "origin": "extracted"},
              "journal": {"value": "Osgoode Hall LJ", "origin": "extracted"}}
    item_id, token, _ = service.item_store.add(source_type="journal_article", fields=fields)
    blocks = service.planned_blocks("这是本书，不是期刊论文", service.turn_context(item_id, token))
    question = next(b for b in blocks if b["type"] == "field_question")
    assert question["source_type"] == "book"
    # journal has no home in the book template and is dropped, not renamed.
    assert {f["label"]: f["origin"] for f in question["known"]} and "Osgoode Hall LJ" not in str(question["known"])
    assert {f["name"] for f in question["fields"]} == {"place", "publisher", "year"}


def test_an_unknown_source_type_is_refused(monkeypatch):
    use_jev(monkeypatch, "change_type")
    use_model(monkeypatch, {"source_type": "manuscript", "message": "好。"})
    assert service.planned_blocks("这是手稿", book_context()) is None


# ── Abstaining ────────────────────────────────────────────────────────


def test_a_new_search_falls_through_to_the_query_pipeline(monkeypatch):
    use_jev(monkeypatch, "new_search")
    use_model(monkeypatch, {"message": ""})
    assert service.planned_blocks("R v Gladue", book_context()) is None


def test_low_confidence_leaves_the_existing_path_in_control(monkeypatch):
    import llm_api.deepseek_api as deepseek
    use_jev(monkeypatch, "provide_fields", confidence=0.2)
    monkeypatch.setattr(deepseek, "ask_deepseek",
                        lambda *a, **kw: pytest.fail("slot call made on an abstention"))
    assert service.planned_blocks("出版于 1998 年", book_context()) is None


def test_without_jev_the_planner_never_runs(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    use_model(monkeypatch, {"values": [{"field": "year", "text": "1998"}], "message": ""})
    assert service.planned_blocks("出版于 1998 年", book_context()) is None


def test_an_empty_context_is_never_planned(monkeypatch):
    use_jev(monkeypatch, "provide_fields")
    assert service.planned_blocks("R v Gladue", service.turn_context()) is None


def test_a_broken_model_reply_falls_back_instead_of_guessing(monkeypatch):
    import llm_api.deepseek_api as deepseek
    use_jev(monkeypatch, "provide_fields")
    monkeypatch.setattr(deepseek, "ask_deepseek", lambda prompt, **kw: "not json at all")
    blocks = service.planned_blocks("出版于 1998 年", book_context())
    # Nothing could be grounded, so the turn asks rather than inventing a value.
    assert "citation_result" not in [b["type"] for b in blocks]
    assert next(b for b in blocks if b["type"] == "field_question")


def test_the_prompt_never_carries_the_rule_templates(monkeypatch):
    use_jev(monkeypatch, "correct_fields")
    prompts = use_model(monkeypatch, {"fields": ["year"], "message": ""})
    service.planned_blocks("年份不对", book_context())
    assert prompts and "segments" not in prompts[0]
    for segment in service.schemas()["book"]["segments"]:
        assert json.dumps(segment) not in prompts[0]


# ── A failed lookup is a conversation, not a form ─────────────────────


def dead_end_context(monkeypatch, query="The Early Upanisads Olivelle"):
    """A session whose lookup just came back empty."""
    monkeypatch.setattr(conversation, "conversation_store", conversation.ConversationStore())
    context = service.turn_context()
    service.ensure_session(context)
    service.remember(context, "user", query)
    return context


def use_resolve_jev(monkeypatch, action, confidence=0.9):
    probabilities = {key: 0.0 for key in conversation.RESOLVE_ACTIONS}
    probabilities[action] = 1.0

    class Stub:
        def __init__(self, *a, **k):
            pass

        def decide_choice(self, state, question):
            assert question.question_id == "resolve_action.v1"
            return DecisionResult(question.question_id, action, probabilities, confidence, "jev-latest", 90.0)

        def close(self):
            pass

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(jev_client, "JevClient", Stub)


def test_a_dead_end_asks_before_assuming_a_type(monkeypatch):
    use_resolve_jev(monkeypatch, "ask_source_type")
    use_model(monkeypatch, {"options": ["book", "journal_article"],
                            "message": "没查到这条。它是一本书，还是期刊上的一篇文章？"})
    context = dead_end_context(monkeypatch)
    blocks = service.fallback_blocks("case_name", "The Early Upanisads Olivelle", context)
    assert [b["type"] for b in blocks] == ["type_question"]
    assert blocks[0]["message"] == "没查到这条。它是一本书，还是期刊上的一篇文章？"
    assert [o["source_type"] for o in blocks[0]["options"]] == ["book", "journal_article"]
    assert [o["label"] for o in blocks[0]["options"]] == ["书籍", "期刊论文"]
    # Crucially: no form was pushed at the user.
    assert "field_question" not in [b["type"] for b in blocks]


def test_the_agent_may_commit_to_a_type_when_the_query_shows_it(monkeypatch):
    use_resolve_jev(monkeypatch, "set_source_type")
    use_model(monkeypatch, {"source_type": "book", "message": "没查到这条，我先按一本书来处理；不对的话告诉我。"})
    context = dead_end_context(monkeypatch)
    blocks = service.fallback_blocks("case_name", "The Early Upanisads Olivelle", context)
    question = next(b for b in blocks if b["type"] == "field_question")
    assert question["source_type"] == "book"
    assert question["message"] == "没查到这条，我先按一本书来处理；不对的话告诉我。"


def test_a_type_the_renderer_does_not_have_falls_back_to_asking(monkeypatch):
    use_resolve_jev(monkeypatch, "set_source_type")
    use_model(monkeypatch, {"source_type": "manuscript", "message": "按手稿处理。"})
    context = dead_end_context(monkeypatch)
    context.unresolved = {"query": "a manuscript", "route": "case_name"}
    plan = conversation.plan_resolution("a manuscript", context)
    assert plan.action == "ask_source_type" and plan.source_type is None and plan.options


def test_the_agent_can_ask_for_a_link_or_a_photo_instead(monkeypatch):
    use_resolve_jev(monkeypatch, "request_evidence")
    use_model(monkeypatch, {"message": "没查到。有判决书链接，或者能拍一张首页吗？"})
    context = dead_end_context(monkeypatch)
    blocks = service.fallback_blocks("case_name", "Made Up v Nonexistent", context)
    assert blocks[0]["type"] == "notice"
    assert blocks[0]["message"] == "没查到。有判决书链接，或者能拍一张首页吗？"


def test_the_users_answer_settles_the_type_and_opens_that_form(monkeypatch):
    """Turn two: the user replies in their own words, not by clicking."""
    use_resolve_jev(monkeypatch, "set_source_type")
    use_model(monkeypatch, {"source_type": "book", "message": "好，按书来处理。"})
    context = dead_end_context(monkeypatch)
    conversation.conversation_store.set_unresolved(
        context.session_id, context.session_token, {"query": "The Early Upanisads", "route": "case_name"})
    context.unresolved = {"query": "The Early Upanisads", "route": "case_name"}

    blocks = service.planned_blocks("是本书", context)
    question = next(b for b in blocks if b["type"] == "field_question")
    assert question["source_type"] == "book"
    # Settled: the conversation is no longer waiting on a type.
    assert conversation.conversation_store.get(context.session_id, context.session_token)["unresolved"] is None


def test_clicking_a_proposed_type_needs_no_model_call(monkeypatch):
    monkeypatch.setattr(conversation, "conversation_store", conversation.ConversationStore())
    monkeypatch.setattr(service, "item_store", service.ItemStore())
    import llm_api.deepseek_api as deepseek
    monkeypatch.setattr(deepseek, "ask_deepseek", lambda *a, **kw: pytest.fail("a click must not call a model"))
    context = service.turn_context()
    service.ensure_session(context)
    question = service.open_source_type(context, "journal_article")[0]
    assert question["type"] == "field_question" and question["source_type"] == "journal_article"


def test_without_an_agent_the_dead_end_degrades_instead_of_hanging(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    context = dead_end_context(monkeypatch)
    blocks = service.fallback_blocks("case_name", "Made Up v Nonexistent", context)
    assert blocks[0]["type"] == "field_question" and blocks[0]["message"] == service.OFFLINE_FALLBACK


def test_the_agent_remembers_what_was_already_said(monkeypatch):
    use_resolve_jev(monkeypatch, "ask_source_type")
    prompts = use_model(monkeypatch, {"options": ["book"], "message": "是书吗？"})
    context = dead_end_context(monkeypatch)
    service.remember(context, "assistant", "没查到这条，它是哪一类资料？")
    service.remember(context, "user", "是 1998 年出版的")
    context.history = conversation.conversation_store.get(context.session_id, context.session_token)["history"]
    service.fallback_blocks("case_name", "The Early Upanisads", context)
    assert prompts and "是 1998 年出版的" in prompts[0] and "没查到这条，它是哪一类资料？" in prompts[0]


def test_the_resolution_prompt_carries_no_rule_templates(monkeypatch):
    use_resolve_jev(monkeypatch, "ask_source_type")
    prompts = use_model(monkeypatch, {"options": ["book"], "message": "是书吗？"})
    context = dead_end_context(monkeypatch)
    service.fallback_blocks("case_name", "The Early Upanisads", context)
    for segment in service.schemas()["book"]["segments"]:
        assert json.dumps(segment) not in prompts[0]
