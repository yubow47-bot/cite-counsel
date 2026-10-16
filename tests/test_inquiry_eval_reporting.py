"""A delivered fallback is not a saved inquiry answer or a quality pass."""
import pytest

from eval.inquiry_eval import answer_status


@pytest.mark.parametrize("inspection", [None, {}, {"object": {"answer": None}},
                                        {"object": {"answer": {"text": "A different saved candidate."}}}])
def test_delivered_fallback_without_matching_saved_answer_is_not_saved(inspection):
    status = answer_status("Review failed; these are the attributed source quotations.", inspection)
    assert status == {"answer_delivered": True, "answer_saved": False,
                      "quality_grade": "pending_independent_review"}


def test_actual_matching_saved_answer_is_saved_but_still_needs_quality_review():
    status = answer_status("A checked conditional answer.\n", {"object": {"answer": {"text": "A checked conditional answer."}}})
    assert status["answer_delivered"] and status["answer_saved"]
    assert status["quality_grade"] == "pending_independent_review"


@pytest.mark.parametrize("answer", ["", " \n "])
def test_empty_delivery_does_not_count_as_completion_or_saving(answer):
    status = answer_status(answer, {"object": {"answer": {"text": answer}}})
    assert not status["answer_delivered"] and not status["answer_saved"]
    assert status["quality_grade"] == "pending_independent_review"
