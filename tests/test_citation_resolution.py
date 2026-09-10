"""Deterministic helpers added for the citation-resolution gaps.

Covers, with no network and no LLM:
  * _extract_case_citation — pulling a citation out of "name + citation" input
  * citation_to_case_id / _split_canlii_citation — CanLII case resolution
  * _jurisdiction_from_citation — jurisdiction read off a citation prefix
  * _canlii_db_order — which CanLII database answers which citation shape
  * _canlii_match_listing — citation / title / schedule matching
  * _extract_jurisdiction — A2AJ dataset → jurisdiction name
  * _case_nickname — famous decisions known by a by-name

Run: pytest tests/test_citation_resolution.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools.a2aj_api import _extract_jurisdiction
from local_tools.canlii_api import citation_to_case_id, _split_canlii_citation
from local_tools.citation_search import (
    _canlii_db_order,
    _canlii_item_to_result,
    _canlii_match_listing,
    _case_nickname,
    _extract_case_citation,
    _jurisdiction_from_citation,
    _normalize_for_match,
    _normalize_statute_citation,
)


# ═════════════════════════════════════════════════════════════════════════════
#  _extract_case_citation — "name + citation" is the ordinary copy-paste shape
# ═════════════════════════════════════════════════════════════════════════════

class TestExtractCaseCitation:
    def test_reporter_after_case_name(self):
        assert _extract_case_citation(
            "Roncarelli v Duplessis [1959] SCR 121"
        ) == "[1959] SCR 121"

    def test_reporter_with_volume_after_case_name(self):
        assert _extract_case_citation(
            "R v Gladue [1999] 1 SCR 688"
        ) == "[1999] 1 SCR 688"

    def test_reporter_without_space_before_bracket(self):
        assert _extract_case_citation(
            "Roncarelli v Duplessis[1959] SCR 121"
        ) == "[1959] SCR 121"

    def test_neutral_after_case_name(self):
        assert _extract_case_citation("R v Ipeelee 2012 SCC 13") == "2012 SCC 13"

    def test_bare_neutral(self):
        assert _extract_case_citation("2012 SCC 13") == "2012 SCC 13"

    def test_canlii_number(self):
        assert _extract_case_citation("2012 CanLII 27167") == "2012 CanLII 27167"

    def test_privy_council_reporter(self):
        assert _extract_case_citation(
            "Edwards v Canada (AG) [1930] AC 124"
        ) == "[1930] AC 124"

    def test_name_only_has_no_citation(self):
        assert _extract_case_citation("Roncarelli v Duplessis") == ""

    def test_empty(self):
        assert _extract_case_citation("") == ""

    def test_none(self):
        assert _extract_case_citation(None) == ""


# ═════════════════════════════════════════════════════════════════════════════
#  CanLII case resolution
# ═════════════════════════════════════════════════════════════════════════════

class TestCitationToCaseId:
    def test_canlii_number(self):
        assert citation_to_case_id("2012 CanLII 27167") == "2012canlii27167"

    def test_scc_neutral(self):
        assert citation_to_case_id("2012 SCC 13") == "2012scc13"

    def test_provincial_neutral(self):
        assert citation_to_case_id("2022 ONCA 39") == "2022onca39"

    def test_surrounding_whitespace_tolerated(self):
        assert citation_to_case_id("  2019 BCCA 1  ") == "2019bcca1"

    def test_reporter_citation_is_not_a_case_id(self):
        assert citation_to_case_id("[1959] SCR 121") is None

    def test_case_name_is_not_a_case_id(self):
        assert citation_to_case_id("Roncarelli v Duplessis") is None

    def test_empty(self):
        assert citation_to_case_id("") is None


class TestSplitCanliiCitation:
    def test_neutral_with_parallel_reporter(self):
        assert _split_canlii_citation(
            "2012 SCC 13 (CanLII), [2012] 1 SCR 433"
        ) == ("2012 SCC 13", "[2012] 1 SCR 433")

    def test_canlii_number_with_court_tag_only(self):
        assert _split_canlii_citation(
            "2012 CanLII 27167 (NL SC)"
        ) == ("2012 CanLII 27167", "")

    def test_canlii_number_with_official_reporter(self):
        assert _split_canlii_citation(
            "1959 CanLII 50 (SCC), [1959] SCR 121"
        ) == ("1959 CanLII 50", "[1959] SCR 121")

    def test_empty(self):
        assert _split_canlii_citation("") == ("", "")


# ═════════════════════════════════════════════════════════════════════════════
#  _jurisdiction_from_citation — the signal the classifier used to throw away
# ═════════════════════════════════════════════════════════════════════════════

class TestJurisdictionFromCitation:
    def test_ontario_revised(self):
        assert _jurisdiction_from_citation("Family Law Act, RSO 1990, c F.3") == "on"

    def test_ontario_annual_dotted(self):
        assert _jurisdiction_from_citation("S.O. 2019, c. 7") == "on"

    def test_ontario_regulation(self):
        assert _jurisdiction_from_citation("RRO 1990, Reg 194") == "on"

    def test_federal(self):
        assert _jurisdiction_from_citation("Criminal Code, RSC 1985, c C-46") == "ca"

    def test_alberta(self):
        assert _jurisdiction_from_citation("Family Law Act, SA 2003, c F-4.5") == "ab"

    def test_bc(self):
        assert _jurisdiction_from_citation("Interpretation Act, RSBC 1996, c 238") == "bc"

    def test_quebec_consolidation_without_year(self):
        """CQLR has no year, so the chapter marker follows the prefix directly —
        the prefix must still be read as "CQLR", not "CQLRcC"."""
        assert _jurisdiction_from_citation("CQLR c C-25.01") == "qc"

    def test_manitoba_consolidation_without_year(self):
        assert _jurisdiction_from_citation("The Family Law Act, CCSM c F20") == "mb"

    def test_yukon(self):
        assert _jurisdiction_from_citation("Some Act, SY 2019, c 1") == "yt"

    def test_nunavut(self):
        assert _jurisdiction_from_citation("Some Act, SNu 2019, c 1") == "nu"

    def test_prose_containing_so_is_not_a_citation(self):
        """Short prefixes are only read inside a full citation shape, so the
        English word "so" can never be mistaken for Statutes of Ontario."""
        assert _jurisdiction_from_citation("so it was a long day") is None

    def test_title_without_citation(self):
        assert _jurisdiction_from_citation("Family Law Act") is None

    def test_empty(self):
        assert _jurisdiction_from_citation("") is None


# ═════════════════════════════════════════════════════════════════════════════
#  _canlii_db_order
# ═════════════════════════════════════════════════════════════════════════════

class TestCanliiDbOrder:
    def test_regulation_citation_targets_the_regulation_database(self):
        assert _canlii_db_order("on", "RRO 1990, Reg 194") == ["onr"]

    def test_o_reg_citation_targets_the_regulation_database(self):
        assert _canlii_db_order("on", "O Reg 194/90") == ["onr"]

    def test_statute_citation_skips_the_regulation_database(self):
        order = _canlii_db_order("on", "SO 2019, c 7")
        assert order[0] == "ons"
        assert "ona" in order
        assert "onr" not in order

    def test_title_only_tries_statutes_then_regulations(self):
        assert _canlii_db_order("on", None) == ["ons", "onr"]

    def test_yukon_resolves_under_the_internal_code(self):
        """The internal jurisdiction code is "yt" while CanLII's own code is
        "yk".  The mismatch used to make every Yukon lookup a silent no-op."""
        assert _canlii_db_order("yt", None) == ["yks", "ykr"]

    def test_unknown_jurisdiction(self):
        assert _canlii_db_order("zz", None) == []


# ═════════════════════════════════════════════════════════════════════════════
#  _canlii_match_listing
# ═════════════════════════════════════════════════════════════════════════════

FLA = {"title": "Family Law Act", "citation": "RSO 1990, c F.3"}
CJA = {"title": "Courts of Justice Act", "citation": "RSO 1990, c C.43"}
RCP = {"title": "Rules of Civil Procedure", "citation": "RRO 1990, Reg 194"}
SCH_7 = {"title": "Cannabis Taxation Coordination Act, 2019",
         "citation": "SO 2019, c 7, Sch 7"}
SCH_17 = {"title": "Crown Liability and Proceedings Act, 2019",
          "citation": "SO 2019, c 7, Sch 17"}
TAXATION = {"title": "Taxation Act, 2007", "citation": "SO 2007, c 11, Sch A"}


class TestCanliiMatchListing:
    def test_citation_match_wins(self):
        path, matches, sel = _canlii_match_listing(
            [FLA, CJA], _normalize_for_match("anything at all"),
            citation="RSO 1990, c F.3",
        )
        assert path == "citation"
        assert matches == [FLA]

    def test_citation_match_tolerates_punctuation_differences(self):
        path, matches, _ = _canlii_match_listing(
            [FLA, CJA], "", citation="R.S.O. 1990, c. F.3",
        )
        assert path == "citation"
        assert matches == [FLA]

    def test_omnibus_chapter_offers_its_schedules(self):
        """"SO 2019, c 7" exists only as its schedules — offer them rather than
        reporting the citation unknown."""
        path, matches, sel = _canlii_match_listing(
            [SCH_7, SCH_17, FLA], "", citation="SO 2019, c 7",
        )
        assert path == "citation_schedules"
        assert matches == [SCH_7, SCH_17]
        assert sel is True

    def test_exact_title_match(self):
        path, matches, sel = _canlii_match_listing(
            [FLA, CJA], _normalize_for_match("Family Law Act"),
        )
        assert path == "exact"
        assert matches == [FLA]
        assert sel is False

    def test_two_identical_titles_refuse_to_guess(self):
        dup = {"title": "Family Law Act", "citation": "SO 2000, c 1"}
        path, matches, sel = _canlii_match_listing(
            [FLA, dup], _normalize_for_match("Family Law Act"),
        )
        assert path == "no_exact_match"
        assert matches == []

    def test_direction_a_residual_gate_accepts_a_province_word(self):
        path, matches, _ = _canlii_match_listing(
            [RCP], _normalize_for_match("Ontario Rules of Civil Procedure"),
        )
        assert path == "direction_a_residual_ok"
        assert matches == [RCP]

    def test_direction_a_residual_gate_rejects_substantive_extra_words(self):
        path, matches, _ = _canlii_match_listing(
            [RCP], _normalize_for_match("Criminal Rules of Civil Procedure"),
        )
        assert path == "no_exact_match"
        assert matches == []

    def test_year_stripped_title_match(self):
        path, matches, _ = _canlii_match_listing(
            [TAXATION], _normalize_for_match("Taxation Act"),
        )
        assert path == "year_stripped_match"
        assert matches == [TAXATION]

    def test_no_match(self):
        path, matches, _ = _canlii_match_listing(
            [FLA], _normalize_for_match("Some Entirely Different Act"),
        )
        assert path == "no_exact_match"
        assert matches == []

    def test_empty_listing(self):
        path, matches, _ = _canlii_match_listing([], "family law act")
        assert path == "no_exact_match"
        assert matches == []


class TestNormalizeStatuteCitation:
    def test_punctuation_and_spacing_collapse(self):
        assert (_normalize_statute_citation("S.O. 2019, c. 7")
                == _normalize_statute_citation("SO 2019 c 7"))

    def test_distinct_citations_stay_distinct(self):
        assert (_normalize_statute_citation("SO 2019, c 7")
                != _normalize_statute_citation("SO 2019, c 7, Sch 7"))

    def test_empty(self):
        assert _normalize_statute_citation("") == ""


class TestCanliiItemToResult:
    def test_chapter_extracted_and_jurisdiction_upper_cased(self):
        r = _canlii_item_to_result(FLA, "on", "s 1", "fallback")
        assert r == {
            "statute_title": "Family Law Act",
            "jurisdiction": "ON",
            "chapter": "c F.3",
            "pinpoint": "s 1",
            "citation": "RSO 1990, c F.3",
            "verified": True,
            "source": "canlii",
        }

    def test_regulation_has_no_chapter(self):
        r = _canlii_item_to_result(RCP, "on", "r 21.01", "fallback")
        assert r["chapter"] is None
        assert r["citation"] == "RRO 1990, Reg 194"


# ═════════════════════════════════════════════════════════════════════════════
#  _extract_jurisdiction — "ON" is a substring of "LEGISLATION"
# ═════════════════════════════════════════════════════════════════════════════

class TestExtractJurisdiction:
    def test_federal(self):
        assert _extract_jurisdiction("LEGISLATION-FED") == "Canada"

    def test_ontario(self):
        assert _extract_jurisdiction("LEGISLATION-ON") == "Ontario"

    def test_alberta_not_mislabelled_ontario(self):
        """Regression: the substring test matched the "ON" inside
        "LEGISLATION", so every non-federal statute came back as Ontario."""
        assert _extract_jurisdiction("LEGISLATION-AB") == "Alberta"

    def test_quebec(self):
        assert _extract_jurisdiction("LEGISLATION-QC") == "Quebec"

    def test_regulations_dataset(self):
        assert _extract_jurisdiction("REGULATIONS-ON") == "Ontario"

    def test_regulations_dataset_other_province(self):
        assert _extract_jurisdiction("REGULATIONS-QC") == "Quebec"

    def test_case_dataset_has_no_jurisdiction(self):
        assert _extract_jurisdiction("SCC") == ""

    def test_empty(self):
        assert _extract_jurisdiction("") == ""


# ═════════════════════════════════════════════════════════════════════════════
#  _case_nickname
# ═════════════════════════════════════════════════════════════════════════════

class TestCaseNickname:
    def test_persons_case(self):
        assert _case_nickname("Persons Case") == "persons case"

    def test_leading_article_stripped(self):
        assert _case_nickname("the persons case") == "persons case"

    def test_upper_case(self):
        assert _case_nickname("PERSONS CASE") == "persons case"

    def test_patriation_reference(self):
        assert _case_nickname("Patriation Reference") == "patriation reference"

    def test_party_named_case_is_not_a_nickname(self):
        """"Gladue case" names a party, so it stays on the case_name route."""
        assert _case_nickname("Gladue case") is None

    def test_ordinary_case_name(self):
        assert _case_nickname("R v Gladue") is None

    def test_empty(self):
        assert _case_nickname("") is None
