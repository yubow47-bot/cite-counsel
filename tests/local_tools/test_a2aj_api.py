"""Tests for a2aj_api._map_fields — deterministic field mapping, no I/O."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from local_tools.a2aj_api import _map_fields
from core.mcgill_engine import select_subpattern


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

    def test_neutral_only_select_subpattern(self):
        """Neutral-only record routes to 'juris.neutral', not 'juris.neutral_parallel'."""
        record = {
            "citation_en": "2011 TCC 223",
            "name_en": "Scarlet Nelson/ Larry Nelson v. The Queen",
            "document_date_en": "2011-04-20T00:00:00+00:00",
            "dataset": "TCC",
        }
        mapped = _map_fields(record)
        sp = select_subpattern("jurisprudence", mapped)
        assert sp == "juris.neutral", f"Expected juris.neutral, got {sp!r}"

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

    def test_neutral_and_reporter_select_subpattern(self):
        """Genuine neutral+reporter record routes to 'juris.neutral_parallel'."""
        record = {
            "citation_en": "2016 SCC 27",
            "citation2_en": "[2016] 1 SCR 631",
            "name_en": "R. v. Jordan",
            "document_date_en": "2016-07-08T00:00:00+00:00",
            "dataset": "SCC",
        }
        mapped = _map_fields(record)
        sp = select_subpattern("jurisprudence", mapped)
        assert sp == "juris.neutral_parallel", f"Expected juris.neutral_parallel, got {sp!r}"

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

    def test_duplicate_citation2_en_select_subpattern(self):
        """Equal-value pre-neutral record routes to 'juris.reported_only' — the
        subpattern whose example is itself a reporter citation.  Routing it to
        'juris.neutral' made the model fabricate a neutral cite to match that
        subpattern's example ("R v King, 2002 SCC 10")."""
        record = {
            "citation_en": "[1999] 1 SCR 688",
            "citation2_en": "[1999] 1 SCR 688",
            "name_en": "R. v. Gladue",
            "document_date_en": "1999-04-23T00:00:00",
            "dataset": "SCC",
        }
        mapped = _map_fields(record)
        sp = select_subpattern("jurisprudence", mapped)
        assert sp == "juris.reported_only", f"Expected juris.reported_only, got {sp!r}"

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
        assert select_subpattern("jurisprudence", mapped) == "juris.reported_only"

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
        assert select_subpattern("jurisprudence", mapped) == "juris.neutral_parallel"

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
        assert select_subpattern("jurisprudence", mapped) == "juris.reported_only"

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
