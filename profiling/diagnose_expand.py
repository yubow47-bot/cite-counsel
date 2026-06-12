"""Run multiple concept queries to sample expand_concept retry rate.

Writes to profiling/expand_retry_log.jsonl (append).
Existing log is NOT cleared — remove it manually for a fresh run.
"""

import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from local_tools.citation_search import expand_concept

CONCEPT_QUERIES = [
    "gladue principle",
    "duty to consult",
    "mens rea",
    "Oakes test",
    "Charter s 7",
    "reasonable expectation of privacy",
    "freedom of expression",
    "proportionality",
    "stare decisis",
    "aboriginal title",
]

RUNS_PER_QUERY = 3

# Ensure log dir exists
log_dir = os.path.join(os.path.dirname(__file__))
os.makedirs(log_dir, exist_ok=True)

total_calls = 0
retry_count = 0

for q in CONCEPT_QUERIES:
    for rn in range(1, RUNS_PER_QUERY + 1):
        total_calls += 1
        try:
            result = expand_concept(q)
            print(f"[{total_calls:2d}] {q:40s} run={rn}  candidates={len(result)}")
        except Exception as e:
            print(f"[{total_calls:2d}] {q:40s} run={rn}  ERROR={e}")
        time.sleep(0.5)

# Report
# Count retries from the log file
log_path = os.path.join(log_dir, "expand_retry_log.jsonl")
if os.path.exists(log_path):
    with open(log_path, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]
    retry_count = len(entries)
    success_after = sum(1 for e in entries if e.get("finally_succeeded"))
    fail_after = retry_count - success_after
    print(f"\n{'='*60}")
    print(f"Total expand_concept calls:     {total_calls}")
    print(f"Retries triggered:              {retry_count}")
    print(f"  -> succeeded after retry:     {success_after}")
    print(f"  -> failed after retry:        {fail_after}")
    print(f"Retry rate:                     {retry_count}/{total_calls} = {retry_count/total_calls*100:.1f}%")
    print(f"Log: {log_path}")
else:
    print("\nNo retry log found (no failures occurred).")
