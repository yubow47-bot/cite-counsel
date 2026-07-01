"""Probe: does A2AJ /fetch tolerate bare (unbracketed) reporter citations?

Reuses the EXISTING a2aj_api.fetch_by_citation() — no new HTTP clients.
Run from project root:
    python scripts/probe_a2aj_bare_citation.py
"""

import io
import json
import os
import sys

# Force UTF-8 stdout (Windows GBK compat)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Ensure project root is on sys.path
_proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _proj not in sys.path:
    sys.path.insert(0, _proj)

from local_tools.a2aj_api import fetch_by_citation
from local_tools.citation_search import classify_and_normalize

SEP = "─" * 72


def try_fetch(label: str, citation: str):
    """Call fetch_by_citation and print whether it returned a usable record."""
    print(f"\n  Input:    {citation!r}")
    result = fetch_by_citation(citation)
    if "error" in result:
        print(f"  Status:   ERROR — {result['error']}")
        return
    if "raw_input" in result:
        print(f"  Status:   NOT FOUND (raw_input returned)")
        return
    # Usable record — show key fields
    name = result.get("style_of_cause") or result.get("statute_title") or "?"
    nc = result.get("neutral_citation", "")
    rep = result.get("reporter", "")
    print(f"  Status:   FOUND")
    print(f"  Name:     {name}")
    print(f"  Neutral:  {nc}")
    print(f"  Reporter: {rep}")


def main():
    print(SEP)
    print("  PROBE A — fetch_by_citation  vs  5 citation variants")
    print(SEP)

    probes = [
        ("1. Bare, lowercase    ", "1986 1 scr 103"),
        ("2. Bare, uppercase    ", "1986 1 SCR 103"),
        ("3. Bracketed          ", "[1986] 1 SCR 103"),
        ("4. Bracketed control  ", "[1999] 1 SCR 688"),
        ("5. Neutral control    ", "2022 SCC 39"),
    ]

    for label, citation in probes:
        print(f"\n{SEP}")
        print(f"  {label}")
        try_fetch(label, citation)

    # ── PROBE B: What does classify_and_normalize say? ──
    print(f"\n\n{SEP}")
    print("  PROBE B — classify_and_normalize(\"1986 1 scr 103\")")
    print(SEP)
    import requests as _req
    try:
        classified = classify_and_normalize("1986 1 scr 103")
        print(f"\n  Result:   {json.dumps(classified, ensure_ascii=False)}")
        print(f"  Route:    {classified['type']}")
        print(f"  Norm'd:   {classified['normalized']}")
    except _req.exceptions.ReadTimeout:
        print("\n  TIMEOUT — DeepSeek API not reachable from this network.")
        print("  (This is an infra issue, not a code bug.)")
    except Exception as e:
        print(f"\n  ERROR:    {e}")

    print(f"\n{SEP}")
    print("  Probe complete.")
    print(SEP)


if __name__ == "__main__":
    main()
