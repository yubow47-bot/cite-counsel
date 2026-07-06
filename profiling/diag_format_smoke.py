"""Live smoke test: format_citation with disable_thinking=True.
3 representative cases: case_name, legislation, constitutional statute.
Compare output against known-correct expectations.
"""

import json
import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))

from core.mcgill_engine import format_citation


def run(label: str, fields: dict, expected_hint: str):
    t0 = time.time()
    result = format_citation(fields)
    elapsed = time.time() - t0
    print(f"[{label}]  latency={elapsed:.1f}s")
    print(f"  Output: {result}")
    if expected_hint in result:
        print(f"  [OK] contains expected text: {expected_hint!r}")
    else:
        print(f"  [FAIL] MISSING expected text: {expected_hint!r}")
    print()


# 1. Case name
run("case_name",
    {"name": "R v Oakes", "neutral_citation": "[1986] 1 SCR 103"},
    "R v Oakes")

# 2. Legislation
run("legislation",
    {"statute_title": "Criminal Code", "jurisdiction": "Canada",
     "chapter": "c C-46", "pinpoint": "s 718.2(e)"},
    "Criminal Code")

# 3. Constitutional statute (highest risk — longest prompt, full rule set)
run("constitutional",
    {"statute_title": "Constitution Act, 1867", "jurisdiction": None,
     "chapter": None, "pinpoint": None},
    "Constitution Act, 1867")
