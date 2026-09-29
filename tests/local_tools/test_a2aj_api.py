"""Tests for a2aj_api._map_fields — deterministic field mapping, no I/O."""
import sys, os
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from local_tools.a2aj_api import _extract_case_citation, _map_fields


class TestMapFieldsReporter:
    """CLASS A and B: reporter mapping from A2AJ records."""

    # ── CLASS A: neutral-only (the bug case) ─────────────────────

    def test_neutral_only_reporter_empty(self):
        """A2AJ record with no citation2_en → reporter is empty."""
        record = {
            "citation_en": "2011 TCC 223",
            "name_en": "Scarlet Nelson/ Larry Nelson v. The Queen",
            "document_date_en": "2011-04-20T00:00:00+00:00",
            "dataset": "TCC",
        }
        mapped = _map_fields(record)
        assert mapped["neutral_citation"] == "2011 TCC 223"
        assert mapped["reporter"] == ""  # NOT backfilled with neutral cite
        assert mapped["style_of_cause"] == "Scarlet Nelson/ Larry Nelson v. The Queen"

    def test_neutral_only_citation2_en_empty_string(self):
        """citation2_en explicitly present but empty → reporter stays empty."""
        record = {
            "citation_en": "2011 TCC 223",
            "citation2_en": "",
            "name_en": "Scarlet Nelson/ Larry Nelson v. The Queen",
            "document_date_en": "2011-04-20T00:00:00+00:00",
            "dataset": "TCC",
        }
        mapped = _map_fields(record)
        assert mapped["neutral_citation"] == "2011 TCC 223"
        assert mapped["reporter"] == ""

    # ── CLASS B: genuine neutral + reporter (must NOT regress) ───

    def test_neutral_and_reporter_populated(self):
        """A2AJ record with BOTH citation_en and citation2_en → reporter is the real parallel cite."""
        record = {
            "citation_en": "2016 SCC 27",
            "citation2_en": "[2016] 1 SCR 631",
            "name_en": "R. v. Jordan",
            "document_date_en": "2016-07-08T00:00:00+00:00",
            "dataset": "SCC",
        }
        mapped = _map_fields(record)
        assert mapped["neutral_citation"] == "2016 SCC 27"
        assert mapped["reporter"] == "[2016] 1 SCR 631"  # distinct from neutral
        assert mapped["reporter"] != mapped["neutral_citation"]

    # ── CLASS C: citation2_en duplicates citation_en (pre-neutral era, Gladue) ──

    def test_duplicate_citation2_en_reporter_empty(self):
        """A2AJ fills both citation fields with the same print citation
        (pre-neutral era, e.g. R v Gladue) → the print citation lands in
        reporter and neutral_citation stays empty, so no neutral citation can
        be invented downstream."""
        record = {
            "citation_en": "[1999] 1 SCR 688",
            "citation2_en": "[1999] 1 SCR 688",
            "name_en": "R. v. Gladue",
            "document_date_en": "1999-04-23T00:00:00",
            "dataset": "SCC",
        }
        mapped = _map_fields(record)
        assert mapped["neutral_citation"] == ""
        assert mapped["reporter"] == "[1999] 1 SCR 688"

    def test_duplicate_citation2_en_dotted_variant(self):
        """Dotted reporter variant (S.C.R.) normalizes to the same citation → reporter emptied."""
        record = {
            "citation_en": "[1999] 1 SCR 688",
            "citation2_en": "[1999] 1 S.C.R. 688",
            "name_en": "R. v. Gladue",
            "document_date_en": "1999-04-23T00:00:00",
            "dataset": "SCC",
        }
        mapped = _map_fields(record)
        assert mapped["neutral_citation"] == ""
        assert mapped["reporter"] == "[1999] 1 SCR 688"

    def test_print_only_citation_goes_to_reporter(self):
        """Single print citation with no citation2_en (Roncarelli) → reporter,
        never neutral_citation."""
        record = {
            "citation_en": "[1959] SCR 121",
            "name_en": "Roncarelli v. Duplessis",
            "document_date_en": "1959-01-27T00:00:00",
            "dataset": "SCC",
        }
        mapped = _map_fields(record)
        assert mapped["neutral_citation"] == ""
        assert mapped["reporter"] == "[1959] SCR 121"

    def test_print_primary_with_neutral_parallel_is_unswapped(self):
        """Reversed A2AJ record (print in citation_en, neutral in citation2_en)
        still slots each by shape rather than by position."""
        record = {
            "citation_en": "[2012] 1 SCR 433",
            "citation2_en": "2012 SCC 13",
            "name_en": "R. v. Ipeelee",
            "document_date_en": "2012-03-23T00:00:00",
            "dataset": "SCC",
        }
        mapped = _map_fields(record)
        assert mapped["neutral_citation"] == "2012 SCC 13"
        assert mapped["reporter"] == "[2012] 1 SCR 433"

    def test_canlii_number_is_a_neutral_citation(self):
        """CanLII-assigned numbers are neutral citations, not reporter cites."""
        record = {
            "citation_en": "2012 CanLII 27167",
            "name_en": "Barrett v. Reardon",
            "document_date_en": "2012-05-22T00:00:00",
            "dataset": "NLSCTD",
        }
        mapped = _map_fields(record)
        assert mapped["neutral_citation"] == "2012 CanLII 27167"
        assert mapped["reporter"] == ""

    def test_two_print_reporters_keeps_the_official_one(self):
        """A pre-neutral case with two DIFFERENT print reporters (SCR + CCC)
        keeps the first (official) one and drops the parallel reporter.

        Deliberate: the subpattern set has no "reporter + parallel reporter"
        form, only juris.reported_only and juris.neutral_parallel.  Routing
        SCR+CCC to juris.neutral_parallel is what put a print citation into the
        neutral slot and made the model fabricate a neutral cite.  Dropping an
        optional parallel reporter is the cheaper loss.
        """
        record = {
            "citation_en": "[1993] 3 SCR 3",
            "citation2_en": "(1993), 83 C.C.C. (3d) 346",
            "name_en": "R. v. Creighton",
            "document_date_en": "1993-08-26T00:00:00+00:00",
            "dataset": "SCC",
        }
        mapped = _map_fields(record)
        assert mapped["neutral_citation"] == ""
        assert mapped["reporter"] == "[1993] 3 SCR 3"

    def test_dotted_neutral_duplicate_collapses(self):
        """A dotted neutral variant must not render as a parallel cite."""
        record = {
            "citation_en": "2012 SCC 13",
            "citation2_en": "2012 SCC. 13",
            "name_en": "R. v. Ipeelee",
            "document_date_en": "2012-03-23T00:00:00",
            "dataset": "SCC",
        }
        mapped = _map_fields(record)
        assert mapped["neutral_citation"] == "2012 SCC 13"
        assert mapped["reporter"] == ""

    # ── Edge: legislation path (not affected, but guard against regression) ──

    def test_legislation_path_reporter_not_set(self):
        """Legislation records do not get a reporter field."""
        record = {
            "citation_en": "RSC 1985, c C-46",
            "name_en": "Criminal Code",
            "dataset": "LEGISLATION_FED",
        }
        mapped = _map_fields(record)
        assert "reporter" not in mapped
        assert mapped["statute_title"] == "Criminal Code"
        assert mapped["citation"] == "RSC 1985, c C-46"
        assert "neutral_citation" not in mapped

    def test_regulations_dataset_maps_to_legislation_schema(self):
        record = {
            "citation_en": "RRO 1990, Reg 194",
            "name_en": "Rules of Civil Procedure",
            "dataset": "REGULATIONS-ON",
        }
        mapped = _map_fields(record)

        assert mapped["citation"] == "RRO 1990, Reg 194"
        assert "neutral_citation" not in mapped
        assert mapped["jurisdiction"] == "Ontario"


def test_search_cases_multi_resolves_an_embedded_citation_directly():
    """"R v Jordan, 2016 SCC 27" is a name search /search cannot match: the
    citation text is not part of the case's name field. The embedded
    citation must be pulled out and resolved by direct lookup instead."""
    from local_tools.a2aj_api import search_cases_multi

    mapped = {"style_of_cause": "R. v. Jordan", "neutral_citation": "2016 SCC 27",
              "reporter": "[2016] 1 SCR 631", "year": "2016", "date": "2016-07-08",
              "url": "https://decisions.scc-csc.ca/scc-csc/scc-csc/en/item/16057/index.do"}

    with patch("local_tools.a2aj_api.fetch_by_citation", return_value=mapped) as fetch, \
         patch("local_tools.utils.request_with_retry") as search_call:
        results = search_cases_multi("R v Jordan, 2016 SCC 27")

    fetch.assert_called_once_with("2016 SCC 27")
    search_call.assert_not_called()          # /search never runs once the direct fetch hits
    assert results == [mapped]


def test_search_cases_multi_falls_back_to_search_when_citation_is_not_found():
    """A citation embedded in the query that A2AJ does not recognize should
    not swallow the request -- /search still runs on the full query."""
    from local_tools.a2aj_api import search_cases_multi

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"results": [{"name_en": "R. v. Jordan", "dataset": "SCC"}]}

    with patch("local_tools.a2aj_api.fetch_by_citation", return_value={"raw_input": "2016 SCC 27"}), \
         patch("local_tools.a2aj_api.request_with_retry", return_value=response) as search_call:
        results = search_cases_multi("R v Jordan, 2016 SCC 27")

    search_call.assert_called_once()
    assert results == [{"name_en": "R. v. Jordan", "dataset": "SCC"}]


def test_search_cases_multi_runs_search_directly_when_no_citation_is_embedded():
    """A bare name (no citation to pull out) is unaffected: straight to /search."""
    from local_tools.a2aj_api import search_cases_multi

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"results": []}

    with patch("local_tools.a2aj_api.fetch_by_citation") as fetch, \
         patch("local_tools.a2aj_api.request_with_retry", return_value=response) as search_call:
        search_cases_multi("R v Jordan")

    fetch.assert_not_called()
    search_call.assert_called_once()


class TestExtractCaseCitation:
    """"name + citation" is the ordinary copy-paste shape."""

    def test_reporter_after_case_name(self):
        assert _extract_case_citation("Roncarelli v Duplessis [1959] SCR 121") == "[1959] SCR 121"

    def test_reporter_with_volume_after_case_name(self):
        assert _extract_case_citation("R v Gladue [1999] 1 SCR 688") == "[1999] 1 SCR 688"

    def test_reporter_without_space_before_bracket(self):
        assert _extract_case_citation("Roncarelli v Duplessis[1959] SCR 121") == "[1959] SCR 121"

    def test_neutral_after_case_name(self):
        assert _extract_case_citation("R v Ipeelee 2012 SCC 13") == "2012 SCC 13"

    def test_bare_neutral(self):
        assert _extract_case_citation("2012 SCC 13") == "2012 SCC 13"

    def test_canlii_number(self):
        assert _extract_case_citation("2012 CanLII 27167") == "2012 CanLII 27167"

    def test_privy_council_reporter(self):
        assert _extract_case_citation("Edwards v Canada (AG) [1930] AC 124") == "[1930] AC 124"

    def test_name_only_has_no_citation(self):
        assert _extract_case_citation("Roncarelli v Duplessis") == ""

    def test_empty_and_none(self):
        assert _extract_case_citation("") == ""
        assert _extract_case_citation(None) == ""
