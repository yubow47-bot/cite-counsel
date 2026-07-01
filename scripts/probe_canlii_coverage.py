"""Probe CanLII coverage vs A2AJ — read-only data exploration.

Run from project root:
    python scripts/probe_canlii_coverage.py

Prints raw API responses for human analysis. NO business logic.
"""

import io
import json
import os
import sys

# Force UTF-8 for stdout (Windows GBK compatibility)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from local_tools.a2aj_api import search_cases_multi
from local_tools.canlii_api import (
    CANLII_BASE,
    get_case_databases,
    get_case_metadata,
    get_legislation_databases,
)

SEP = "=" * 74
SUB = "-" * 74


def load_key() -> str:
    load_dotenv()
    key = os.environ.get("CANLII_API_KEY", "")
    if not key:
        print("CANLII_API_KEY not configured — set it in .env and try again.")
        sys.exit(1)
    return key


# ────────────────────────────────────────────────────────────────────────────
# SECTION 1 — A2AJ raw responses for old cases without neutral citations
# ────────────────────────────────────────────────────────────────────────────

def section_1():
    print(SEP)
    print("SECTION 1: A2AJ raw responses for old cases (no neutral citation)")
    print(SEP)

    cases = [
        "Roncarelli v Duplessis",
        "Donoghue v Stevenson",
        "Reference re Secession of Quebec",
    ]

    for name in cases:
        print(f"\n{SUB}")
        print(f"  Query: {name}")
        print(SUB)

        results = search_cases_multi(name, size=5, search_type="name")

        if not results:
            print("  NOT FOUND in A2AJ")
            continue

        print(f"  Found {len(results)} result(s). First result full dict:\n")
        print(json.dumps(results[0], ensure_ascii=False, indent=2))


# ────────────────────────────────────────────────────────────────────────────
# SECTION 2 — CanLII legislationBrowse coverage
# ────────────────────────────────────────────────────────────────────────────

def section_2(key: str):
    print(f"\n\n{SEP}")
    print("SECTION 2: CanLII legislationBrowse coverage")
    print(SEP)

    # --- 2a: Database list ---
    print(f"\n{SUB}")
    print("  2a. Legislation database catalogue")
    print(SUB)

    leg_result = get_legislation_databases()
    if "error" in leg_result:
        print(f"  FAILED: {leg_result['error']}")
        return

    # Debug: print full raw response to understand shape
    databases = leg_result.get("legislationDatabases", [])
    if not databases:
        databases = leg_result.get("databases", [])
    print(f"  Found {len(databases)} databases")

    total = len(databases)
    print(f"\n  Total databases: {total}\n")

    # Group by jurisdiction
    by_juris: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for db in databases:
        j = db.get("jurisdiction", "?")
        by_juris[j] = by_juris.get(j, 0) + 1
        t = db.get("type", "?")
        by_type[t] = by_type.get(t, 0) + 1

    print("  By jurisdiction:")
    for j in sorted(by_juris, key=by_juris.get, reverse=True):
        print(f"    {j}: {by_juris[j]}")
    print(f"\n  By type:")
    for t in sorted(by_type, key=by_type.get, reverse=True):
        print(f"    {t}: {by_type[t]}")

    # Full listing
    print(f"\n  Full database listing (databaseId | jurisdiction | type | name):")
    for db in databases:
        did = db.get("databaseId", "?")
        jur = db.get("jurisdiction", "?")
        typ = db.get("type", "?")
        nam = db.get("name", "?")
        print(f"    {did:20s} | {jur:4s} | {typ:20s} | {nam}")

    # --- 2b: Browse specific legislation databases ---
    print(f"\n\n{SUB}")
    print("  2b. Browse sample legislation databases")
    print(SUB)

    # Pick two databases: "ons" (Ontario statutes) and a Canada statutes db
    target_ids = ["ons"]

    # Find a Canada (ca) statutes database
    ca_statute_db = None
    for db in databases:
        if db.get("jurisdiction") == "ca" and db.get("type") == "STATUTE":
            ca_statute_db = db.get("databaseId", "")
            break

    if ca_statute_db:
        target_ids.append(ca_statute_db)
        print(f"\n  Using '{ca_statute_db}' for Canada statutes.")
    else:
        print("\n  WARNING: No ca/STATUTE database found — skipping second sample.")

    for db_id in target_ids:
        print(f"\n  --- legislationBrowse: {db_id} ---")
        try:
            resp = requests.get(
                f"{CANLII_BASE}/legislationBrowse/en/{db_id}/",
                params={"api_key": key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("legislations", [])
        except requests.exceptions.RequestException as e:
            print(f"    REQUEST FAILED: {e}")
            continue
        except Exception as e:
            print(f"    PARSE FAILED: {e}")
            continue

        count = len(results)
        print(f"    Total legislation items: {count}")
        print(f"    First {min(5, count)} item(s):")
        for item in results[:5]:
            print(json.dumps(item, ensure_ascii=False, indent=4))
            print()


# ────────────────────────────────────────────────────────────────────────────
# SECTION 3 — caseId conversion hypothesis verification
# ────────────────────────────────────────────────────────────────────────────

def section_3():
    print(f"\n\n{SEP}")
    print("SECTION 3: caseId conversion hypothesis verification")
    print(SEP)

    # --- 3a: Dunsmuir via get_case_metadata ---
    print(f"\n{SUB}")
    print('  3a. get_case_metadata("csc-scc", "2008scc9") — Dunsmuir v New Brunswick')
    print(SUB)

    result = get_case_metadata("csc-scc", "2008scc9")
    print()
    if "error" in result:
        print(f"  FAILED: {result['error']}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    # --- 3b: Human-readable analysis notes ---
    print(f"\n\n{SUB}")
    print("  3b. CanLII caseId conversion analysis notes")
    print(SUB)
    print("")
    print("  SECTION 1 A2AJ results - citation mapping feasibility:")
    print("")
    print("  For each case searched above, examine its returned fields.")
    print("  Key indicators:")
    print("")
    print('    [OK] Neutral present "YYYY SCC NN" -> caseId derivable by formula')
    print('         e.g. "2008 SCC 9" -> "2008scc9" (verified in 3a above).')
    print("")
    print("    [OK] CanLII url in result -> caseId extractable from URL path.")
    print("")
    print("    [NO] SCR only (e.g. [1959] SCR 121, [1998] 2 SCR 217)")
    print("         -> caseId NOT derivable by formula.")
    print("         -> needs a title+year search on CanLII caseBrowse.")
    print("")


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────

def main():
    key = load_key()

    section_1()
    section_2(key)
    section_3()

    print(f"\n{SEP}")
    print("Probe complete — inspect output above for analysis.")
    print(SEP)


if __name__ == "__main__":
    main()
