"""Adversarial provenance tests through the same tool dispatch used by the model."""
import json

import pytest

from core.tool_contracts import Artifact, Derivation, Field, Record, is_grounded
from harness.core import Harness
from harness.plugin import discover


@pytest.fixture
def workspace(tmp_path):
    h = Harness(discover(), model="test", config_path=tmp_path / "harness.json")
    h.config["enabled"] = list(h.plugins)
    session, _ = h.sessions.start()
    session.loaded = list(h.plugins)
    session.messages.append({"role": "user", "content": "Please cite the case."})
    ref = session.records.put(Record("jurisprudence", {
        "style_of_cause": Field("R v Smith", "database", source_id="a2aj:smith"),
        "neutral_citation": Field("2020 SCC 1", "database", source_id="a2aj:smith"),
        "full_text": Field("[1] The judgment discusses R v Jordan, 2016 SCC 27 at para 12; "
                           "and R v Gladue, [1999] 1 SCR 688.", "database", source_id="a2aj:smith"),
    }, "a2aj", "smith"))
    return h, session, ref


def call(workspace, name, **params):
    h, session, _ = workspace
    return h._execute(session, {"id": "test", "type": "function",
                               "function": {"name": name, "arguments": json.dumps(params)}})[1].content


@pytest.mark.parametrize("pinpoint", ["at para 1", "at para 12"])
def test_text_occurrence_never_makes_model_pinpoint_user_supplied(workspace, pinpoint):
    result = call(workspace, "mcgill__cite", ref=workspace[2], pinpoint=pinpoint)
    assert result["verified"] is False
    assert "supplied by the assistant" in result["pinpoint_added_by_you"]


@pytest.mark.parametrize("name,value,origin", [
    ("reporter", "[1999] 1 SCR 688", "extracted"),
    ("neutral_citation", "2016 SCC 2", "model"),
])
def test_full_text_cannot_verify_mixed_or_truncated_citation(workspace, name, value, origin):
    composed = call(workspace, "record__compose", record_type="jurisprudence", fields=[
        {"name": "style_of_cause", "value": "R v Jordan", "source": workspace[2]},
        {"name": name, "value": value, "source": workspace[2]},
    ])
    assert composed["written"][name]["origin"] == origin
    cited = call(workspace, "mcgill__cite", ref=composed["ref"])
    assert cited["verified"] is False
    bibliography = call(workspace, "bibliography__build", refs=[cited["ref"]])
    assert bibliography["unverified"] == 1


def test_database_audit_requires_complete_field(workspace):
    h, session, _ = workspace
    field, _ = session.records.resolve("2016 SCC 2")
    assert field.origin == "model"
    field, _ = session.records.resolve("2016 SCC 27")
    assert field.origin == "extracted"
    ref = h.context(session, "mcgill").save(Artifact("citation_text", "2016 SCC 2",
        Derivation((Field("2016 SCC 2", "database", source_id="a2aj:smith"),), "cite.render.v1")))
    assert not is_grounded(session.records.get(ref).derivation)
    facts = h._annotate_reply(session, "2016 SCC 2")
    assert next(f for f in facts if f["fact"] == "2016 SCC 2")["verdict"] == "unsourced"


def test_copying_complete_fields_of_one_work_remains_database_sourced(workspace):
    composed = call(workspace, "record__compose", record_type="jurisprudence", fields=[
        {"name": "style_of_cause", "value": "R v Smith", "source": workspace[2]},
        {"name": "neutral_citation", "value": "2020 SCC 1", "source": workspace[2]},
    ])
    assert call(workspace, "mcgill__cite", ref=composed["ref"])["verified"] is True


def test_exact_fields_from_different_works_are_not_verified(workspace):
    other = workspace[1].records.put(Record("jurisprudence", {
        "reporter": Field("[1999] 1 SCR 688", "database", source_id="a2aj:gladue")}, "a2aj", "gladue"))
    composed = call(workspace, "record__compose", record_type="jurisprudence", fields=[
        {"name": "style_of_cause", "value": "R v Smith", "source": workspace[2]},
        {"name": "reporter", "value": "[1999] 1 SCR 688", "source": other},
    ])
    assert call(workspace, "mcgill__cite", ref=composed["ref"])["verified"] is False


def test_pinpoint_artifact_is_bound_to_its_work(workspace):
    located = call(workspace, "quote__check", ref=workspace[2], quote="The judgment discusses R v Jordan")
    pin = located["pinpoint_ref"]
    assert call(workspace, "mcgill__cite", ref=workspace[2], pinpoint_from=pin)["verified"] is True
    other = workspace[1].records.put(Record("jurisprudence", {
        "style_of_cause": Field("R v Other", "database", source_id="a2aj:other"),
        "neutral_citation": Field("2021 SCC 1", "database", source_id="a2aj:other")}, "a2aj", "other"))
    assert "not located in this cited work" in call(workspace, "mcgill__cite", ref=other, pinpoint_from=pin)["error"]


def test_charter_fixed_form_needs_no_source_record(workspace):
    result = call(workspace, "mcgill__cite", title="Charter")
    assert result["citation"] == ("*Canadian Charter of Rights and Freedoms*, Part I of the "
        "*Constitution Act, 1982*, being Schedule B to the *Canada Act 1982* (UK), 1982, c 11.")
    assert result["format_checked"] is True and result["verified"] is False
    result_with_pin = call(workspace, "mcgill__cite", title="Charter", pinpoint="s 7")
    assert "supplied by the assistant" in result_with_pin["pinpoint_added_by_you"]
    bibliography = call(workspace, "bibliography__build", refs=[result_with_pin["ref"]])
    assert bibliography["count"] == 1 and bibliography["unverified"] == 1


def test_whitespace_removal_cannot_join_different_tokens(workspace):
    ref = workspace[1].records.put(Record("website", {
        "text": Field("PART ICanadian Charter", "extracted", source_id="page:1")}, "web", "page:1"))
    composed = call(workspace, "record__compose", record_type="constitutional", fields=[
        {"name": "part", "value": "PART I", "source": ref, "quote": "PART I Canadian Charter"}])
    assert composed["written"]["part"]["origin"] == "model"
