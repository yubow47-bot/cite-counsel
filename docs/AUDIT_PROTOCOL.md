# Audit Protocol

_Authoritative as of: 2026-06-15. Changes require Opus approval._

---

## Workflow
Opus designs → Claude Code implements → Claude Code produces REVIEW_REQUEST.md

→ Shixian sends to ChatGPT → ChatGPT returns Verdict

→ Shixian reports to Opus → Opus rules

ChatGPT receives REVIEW_REQUEST.md only. No verbal context. No supplementary files unless explicitly listed in the request.

---

## Mandatory Review Inputs

ChatGPT MUST receive all of the following. If any is missing, return the request and state which item is absent:

1. **Goal** — What the change is intended to achieve
2. **Files Changed** — List of modified files with one-line descriptions
3. **Git Diff** — Complete `git diff` output, unabridged
4. **Acceptance Criteria** — What must be true for the change to be considered correct
5. **Test Evidence** — Raw pass/fail output, not narratives
6. **Regression Surface** — Which existing behaviors could be affected

Optional (include only if directly relevant):
- Open Items
- Architecture Impact
- Risk Declaration
- Audit Gaps
- Rollback Plan

---

## Audit Output Format

ChatGPT returns exactly three sections:
Verdict
[Pass | Conditional Pass | Reject]
Risks
[Numbered list. Each risk: what it is, where in the diff it originates, severity.]
Recommended Actions
[Numbered list. Actionable only. No architecture proposals unless Opus requested them.]

No other sections. No implementation summaries. No praise.

---

## Verdict Criteria

| Verdict | Condition |
|---|---|
| **Pass** | All acceptance criteria met, no regressions identified, no unresolved risks |
| **Conditional Pass** | Minor risks present, all addressable without architecture change, Opus may proceed with noted caveats |
| **Reject** | Acceptance criteria not met; OR regression introduced; OR architecture violated; OR determinism broken |

---

## Specific Review Requirements

### Architecture Review
- Does the change respect the north-star architecture: classify → route → get_rules → format?
- Does it introduce any new deviation from this pipeline?
- Does it add LLM calls where deterministic logic was specified?

### Determinism Review
- Any logic that was specified as deterministic (subpattern routing, bill assembly, CrossRef path) must not contain LLM calls or probabilistic branches.
- Flag any `ask_deepseek` / `call_llm` call inside routing or assembly functions.

### State-Machine Review
- Does the change respect `SYSTEM_STATE_MACHINE.md`?
- Does it introduce new states without Opus approval?
- Are all transitions accounted for?

### API Contract Review
- Does the change respect `api_contract.md`?
- Are all existing endpoints preserved with identical signatures?
- Are debug fields still null in production mode?

### Regression Review
- Does `eval/regression_test.py` still pass in full?
- Are the 3 original seed cases unaffected?
- Does the change touch any shared utility (detect_type, get_rules, build_prompt, format_citation) that could silently affect other routes?

---

## Prohibited Inputs from Claude Code

ChatGPT must disregard and flag the following if present in REVIEW_REQUEST.md:

- Implementation summaries or explanations of how the code works
- Self-assessments of correctness
- Architecture proposals
- Future improvement suggestions
- "What I tested" narratives
- Conclusions about whether the change is correct

If any of the above are present, note it under Risks as a protocol violation.
