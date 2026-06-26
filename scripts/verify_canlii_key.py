"""Verify CanLII API key connectivity.

Standalone dev script — not part of the main application flow.
Run manually after setting CANLII_API_KEY in .env:

    python scripts/verify_canlii_key.py
"""

import os
import sys

from dotenv import load_dotenv

# Allow running from project root: python scripts/verify_canlii_key.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from local_tools.canlii_api import (
    get_case_databases,
    get_legislation_databases,
)


def main():
    load_dotenv()

    key = os.environ.get("CANLII_API_KEY", "")
    if not key:
        print("CANLII_API_KEY not configured — set it in .env and try again.")
        sys.exit(1)

    # --- Case databases ---
    print("Checking case databases...")
    case_result = get_case_databases()
    if "error" in case_result:
        print(f"  FAILED: {case_result['error']}")
        sys.exit(1)

    databases = case_result.get("databases", [])
    print(f"  OK — {len(databases)} databases found")
    for db in databases[:5]:
        print(f"    - {db.get('databaseId', '?')}")

    # --- Legislation databases ---
    print("\nChecking legislation databases...")
    leg_result = get_legislation_databases()
    if "error" in leg_result:
        print(f"  FAILED: {leg_result['error']}")
        sys.exit(1)

    leg_databases = leg_result.get("databases", [])
    print(f"  OK — {len(leg_databases)} databases found")
    for db in leg_databases[:5]:
        print(f"    - {db.get('databaseId', '?')}")

    # --- Summary ---
    if databases and leg_databases:
        print("\n✅ CanLII key 验证成功")
    else:
        print("\n⚠️  CanLII key 有效但返回列表为空")
        sys.exit(1)


if __name__ == "__main__":
    main()
