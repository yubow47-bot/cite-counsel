"""Adversarial evidence chains must retain their unsupported dependencies."""

import pytest

from core.tool_contracts import Artifact, Derivation, Field, Finding, Record, grounding_issues, is_grounded


def source():
    return Field("2026-01-01", "database", source_id="canlii:case:1")


@pytest.mark.parametrize("origin", ["user", "extracted"])
def test_computation_cannot_launder_unsupported_input(origin):
    calculation = Derivation((Field("2026-01-01", origin),), "add-days:v1", ("date",))
    computed = Field("2026-01-31", "computed", rule_id="add-days:v1", derivation=calculation)
    proof = Derivation((computed,), "citation:v1", ("deadline",))
    assert not is_grounded(proof)
    assert [(i.path, i.reason) for i in grounding_issues(proof)] == [
        (("deadline", "computation", "date"), "unverified_" + origin)
    ]


def test_database_leaf_requires_source_reference():
    proof = Derivation((Field("case", "database"),), "cite:v1")
    assert [(i.path, i.reason) for i in grounding_issues(proof)] == [
        (("0",), "missing_source_id")
    ]


@pytest.mark.parametrize("source_id", ["", "   ", 42])
def test_invalid_source_identifiers_rejected(source_id):
    with pytest.raises(ValueError):
        Field("case", "database", source_id=source_id)


def test_empty_proof_and_computation_without_inputs_fail_closed():
    empty = Derivation((), "constant:v1")
    assert not is_grounded(empty)
    assert grounding_issues(empty)[0].reason == "empty_derivation"
    missing = Derivation((Field("result", "computed", rule_id="constant:v1"),), "outer:v1")
    assert grounding_issues(missing)[0].reason == "missing_computation_inputs"
    wrapped = Derivation((Field("result", "computed", rule_id=empty.rule_id, derivation=empty),), "outer:v1")
    assert not is_grounded(wrapped)


def test_computation_requires_matching_rule_and_preserves_other_issues():
    inner = Derivation((Field("guess", "user"),), "actual:v1")
    outer = Derivation((Field("output", "computed", rule_id="other:v1", derivation=inner),), "outer:v1")
    assert [i.reason for i in grounding_issues(outer)] == [
        "computation_rule_mismatch", "unverified_user"
    ]


def test_deep_composition_does_not_depend_on_python_recursion_limit():
    proof = Derivation((source(),), "leaf:v1")
    for _ in range(1500):
        proof = Derivation((proof,), "compose:v1")
    assert is_grounded(proof)


def test_shared_dependency_is_not_a_cycle():
    leaf = source()
    shared = Derivation((leaf, leaf), "combine:v1")
    assert is_grounded(Derivation((shared, shared), "compose:v1"))


def test_cycle_is_reported_without_hanging():
    proof = Derivation((source(),), "compose:v1", ("parent",))
    # Simulate a malformed graph crossing a deserialization/plugin boundary.
    object.__setattr__(proof, "inputs", (proof,))
    assert [(i.path, i.reason) for i in grounding_issues(proof)] == [
        (("parent",), "cyclic_derivation")
    ]


def test_all_unsupported_fields_have_stable_named_paths():
    nested = Derivation((Field("a", "user"), Field("b", "extracted")), "extract:v1", ("title", "date"))
    proof = Derivation((source(), nested), "document:v1", ("authority", "citation"))
    assert [(i.path, i.reason) for i in grounding_issues(proof)] == [
        (("citation", "title"), "unverified_user"),
        (("citation", "date"), "unverified_extracted"),
    ]


def test_model_origin_is_valid_but_never_grounded():
    """A model-supplied value is an honest confession, not evidence: valid to
    build on, always unverified."""
    proof = Derivation((Field("guess", "model"),), "cite:v1", ("court",))
    assert [(i.path, i.reason) for i in grounding_issues(proof)] == [(("court",), "unverified_model")]


@pytest.mark.parametrize("origin", ["verified", "Database", "", None, 1])
def test_unknown_origins_rejected(origin):
    with pytest.raises(ValueError):
        Field("value", origin)


@pytest.mark.parametrize("value", [None, 0, b"text"])
def test_nontext_field_values_rejected(value):
    with pytest.raises(ValueError):
        Field(value, "user")


@pytest.mark.parametrize("span", [(-1, 2), (1, 1), (2, 1), (True, 2), (0, 1.5)])
def test_invalid_source_spans_rejected(span):
    with pytest.raises(ValueError):
        Field("text", "extracted", span=span)


@pytest.mark.parametrize("rule", ["", " ", None, 1])
def test_derivation_requires_rule_identifier(rule):
    with pytest.raises(ValueError):
        Derivation((source(),), rule)


@pytest.mark.parametrize("names", [("one",), ("same", "same"), ("", "valid"), (1, "valid")])
def test_input_names_must_unambiguously_identify_dependencies(names):
    with pytest.raises(ValueError):
        Derivation((source(), source()), "compose:v1", names)


def test_noncomputed_field_cannot_hide_derivation():
    with pytest.raises(ValueError):
        Field("value", "database", source_id="id", derivation=Derivation((source(),), "x:v1"))


def test_invalid_dependency_rejected_at_construction():
    with pytest.raises(ValueError):
        Derivation(("unsupported",), "compose:v1")


@pytest.mark.parametrize("verdict", ["confirmed", "contradicted", "inconclusive"])
@pytest.mark.parametrize("coverage", ["complete", "partial"])
def test_finding_allows_declared_verdict_and_coverage(verdict, coverage):
    finding = Finding(verdict, "detail", coverage, Derivation((source(),), "check:v1"))
    assert finding.verdict == verdict
    assert finding.coverage == coverage


@pytest.mark.parametrize("verdict,coverage", [("verified", "complete"), ("confirmed", "full"), (None, "partial")])
def test_finding_rejects_unknown_statuses(verdict, coverage):
    with pytest.raises(ValueError):
        Finding(verdict, "detail", coverage, Derivation((source(),), "check:v1"))


def test_computed_field_rejects_dictionary_as_derivation():
    with pytest.raises(ValueError):
        Field("result", "computed", derivation={})


def test_record_copies_fields_and_prevents_mutation():
    original = source()
    fields = {"date": original}
    record = Record("case", fields, "canlii", "1")
    fields["date"] = Field("unverified replacement", "user")
    fields["extra"] = Field("extra", "user")
    assert dict(record.fields) == {"date": original}
    with pytest.raises(TypeError):
        record.fields["date"] = fields["date"]


@pytest.mark.parametrize("derivation", [None, {}, "proof"])
def test_artifact_and_finding_require_derivations(derivation):
    with pytest.raises(ValueError):
        Artifact("citation", "content", derivation)
    with pytest.raises(ValueError):
        Finding("confirmed", "detail", "partial", derivation)
