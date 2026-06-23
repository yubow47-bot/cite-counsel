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
