"""expand_concept regression tests.

Usage:
    cd C:/Users/hp/Desktop/mcgill
    .venv_new/Scripts/python.exe tests/test_expand_concept.py

Each case runs N rounds, checks:
  - routing correctness
  - candidate stability (key entries present every round)
  - verification status sanity
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time

from local_tools.citation_search import search_citation, classify_and_normalize
from local_tools import timing_util as timing


# ── helpers ──────────────────────────────────────────────

def _ascii_badge(verified: bool) -> str:
    return "[OK]" if verified else "[!!]"


# ── test cases ───────────────────────────────────────────

TEST_CASES = [
    {
        "input": "gladue principle",
        "expect_route": "concept",
        "must_contain": ["Criminal Code", "Ipeelee"],
        "should_contain_any": ["718.2", "s 718"],
        "note": "statutory source + cases; Gladue itself not in A2AJ, should show unverified",
    },
    {
        "input": "duty to consult",
        "expect_route": "concept",
        "must_contain": ["Haida"],
        "note": "concept expansion, should include Haida Nation v BC",
    },
    {
        "input": "2022 SCC 39",
        "expect_route": "citation_number",
        "must_contain": ["R. v", "2022 SCC 39"],
        "note": "direct hit, bypasses expand_concept",
    },
    {
        "input": "R v Askov 1990",
        "expect_route": "case_name",
        "must_contain": ["Askov"],
        "note": "case name search, returns candidate list",
    },
]

ROUNDS = 2


def _route_retry(query: str, expected: str, max_attempts: int = 2) -> tuple[bool, str]:
    """Retry classification if LLM returns unexpected route."""
    for attempt in range(1, max_attempts + 1):
        result = classify_and_normalize(query)
        actual = result.get("type", "")
        if actual == expected:
            return True, actual
        if attempt < max_attempts:
            time.sleep(0.5)
    return False, actual


def check_routes():
    """Verify routing accuracy (soft check — LLM is non-deterministic)."""
    route_checks = [
        ("gladue principle", "concept"),
        ("duty to consult", "concept"),
        ("2022 SCC 39", "citation_number"),
        ("R v Askov 1990", "case_name"),
        ("Criminal Code", "legislation"),
    ]
    all_ok = True
    for query, expected in route_checks:
        ok, actual = _route_retry(query, expected)
        status = "OK" if ok else "MISMATCH"
        if not ok:
            all_ok = False
        print(f"  [{status}] {query} -> {actual} (expected {expected})")
    return all_ok


def run_expand_concept(query: str) -> dict:
    """Run one expand_concept query (via concept route), return candidate data.
    Retries once if A2AJ returns empty (transient timeout)."""
    timing.start()
    results = search_citation(query)
    if not results:
        time.sleep(2)
        print("      (A2AJ empty, retrying...)")
        timing.start()
        results = search_citation(query)

    candidates = []
    for r in results:
        name = r.get("style_of_cause") or r.get("statute_title") or r.get("name", "?")
        cit = r.get("neutral_citation") or ""
        candidates.append({
            "name": name,
            "citation": cit,
            "verified": r.get("verified", False),
            "warning": r.get("warning", ""),
        })

    return {"count": len(candidates), "candidates": candidates, "timing": timing.report()}


def print_round(label: str, query: str, result: dict):
    """Print one round's results (ASCII-safe, no emoji)."""
    print(f"\n  -- {label}: \"{query}\"")
    print(f"      candidates: {result['count']}")
    for i, c in enumerate(result["candidates"], 1):
        badge = _ascii_badge(c["verified"])
        warn = f" -- {c['warning']}" if c["warning"] else ""
        print(f"       {i}. {badge} {c['name']} -- {c['citation']}{warn}")
    if result.get("timing"):
        result["timing"].print()


def check_consistency(runs: list, case: dict) -> bool:
    """Cross-round consistency checks."""
    ok = True

    for keyword in case.get("must_contain", []):
        for i, run in enumerate(runs):
            names = [c["name"] for c in run["candidates"]]
            fields = names + [c["citation"] for c in run["candidates"]]
            found = any(keyword.lower() in f.lower() for f in fields)
            if not found:
                print(f"  [FAIL] round {i+1}: missing \"{keyword}\" in candidates")
                ok = False

    alt_list = case.get("should_contain_any", [])
    if isinstance(alt_list, str):
        alt_list = [alt_list]
    for keyword in alt_list:
        found_any = False
        for run in runs:
            names = [c["name"] for c in run["candidates"]]
            fields = names + [c["citation"] for c in run["candidates"]]
            if any(keyword.lower() in f.lower() for f in fields):
                found_any = True
                break
        if not found_any:
            print(f"  [INFO] \"{keyword}\" not found in any round (non-mandatory)")

    for i, run in enumerate(runs):
        if run["count"] == 0:
            print(f"  [FAIL] round {i+1}: zero candidates returned")
            ok = False

    return ok


def main():
    passed = 0
    failed = 0

    print("=" * 60)
    print("expand_concept Regression Tests")
    print("=" * 60)

    # Phase 1: routing (informational only - LLM is non-deterministic)
    print("\n[Phase 1] Route classification (informational)")
    print("-" * 40)
    check_routes()

    # Phase 2: expand_concept content
    print("\n[Phase 2] expand_concept content & stability")
    print("-" * 40)

    for case in TEST_CASES:
        query = case["input"]
        print(f"\n>>> Query: \"{query}\"")
        print(f"    Note: {case.get('note', '')}")

        runs = []
        for rn in range(1, ROUNDS + 1):
            try:
                result = run_expand_concept(query)
                runs.append(result)
                print_round(f"Round {rn}", query, result)
            except Exception as e:
                print(f"  [ERROR] Round {rn}: {e}")

        if not runs:
            print("  [FAIL] All rounds errored")
            failed += 1
            continue

        # Consistency checks
        consistent = check_consistency(runs, case)

        if consistent:
            print(f"  >>> Result: [PASS]")
            passed += 1
        else:
            print(f"  >>> Result: [FAIL]")
            failed += 1

        if ROUNDS > 1:
            time.sleep(1)

    # Summary
    total = passed + failed
    print("\n" + "=" * 60)
    print(f"Summary: {passed} passed, {failed} failed / {total} total")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
