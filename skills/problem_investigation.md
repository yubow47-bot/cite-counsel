# Investigating an open question
> Investigate decisive unknowns and preserve answers with their conditions.

Start with the user's decision. Prefer one or two checks. Keep unknown facts
unknown, and preserve supplied givens.

Identify one to three concrete premises that could change a key conclusion.
State what changes if each is true or false, retaining other necessary
conditions. Prioritize unknowns that change the user's decision. Investigate
available sources now; ask a precise question for facts only the user knows.
Answer supported parts.

Fetch each supplied primary URL relevant to a decisive rule or its scope, then use inquiry__conclude after
reading decisive passages. Supply the answer,
one or two key checks, exact quotes, reasoning, objection and its tested
response. Include up to three premises per check: statement, if_true, if_false,
next_check, method and status. Reuse premise_ids. No separate bookkeeping
calls are required. Use task_ids for existing tasks; others stay open.
Use observations for short exact user/source quotes, retaining attribution.
For numeric comparisons, supply calculations with operation, before, after,
an exact quote containing both inputs, and source_ref when from a source.
The server computes and includes those results even in a conditional answer.
Free prose facts and a direct label do not establish truth. Review both premise branches for overreach, without
upgrading the premise's status or evidence. Never invent a quote.

Use claim_kind=direct for a literal source statement,
inference for a conclusion dependent on premises, and hypothesis for a
possible explanation. A descriptive calculation does not establish causation.
Keep untested explanations hypothetical. Unestablished does not mean false;
a plausible alternative is not an observed or more likely cause. Put limits
in the conclusion itself, not merely in a general caveat at the end.

For continuing work, inquiry__premise saves a decisive unknown. Omit ref to
use this turn's inquiry or begin one; use the returned current ref for updates.
Omit id for a new premise; reuse its id when facts arrive. The tool records a
plan: follow next_check with retrieval, reading or a focused user question.

Keep status unknown until actual support or counterevidence exists. With
method=source, evidence_refs identify real source Records and actual receipts
are bound automatically. With method=user, user_quote must exactly quote the
user. Supported/refuted remain model assessments. source_bound means bound
to retrieved material; user_reported means supplied by the user. Neither
certifies truth or legal applicability. Self-written claims are not sources.

Read exceptions, dates, definitions and procedure. Seek counterevidence.
Plans are not execution; failures are not clean misses; no match does not
establish nonexistence or complete coverage.

Distinguish source statements, user reports, assumptions, inferences and
values. Exact source binding does not establish semantic support.

When material arrives, inspect the inquiry, update the relevant premise id
with exact evidence_refs or user_quote, and reassess linked claims. Use
claim_ref in conclude to revise an existing claim. Updating a premise alone
does not renew its old inference; unrelated claims retain their state.
Use effective_verdict and needs_recheck. Unknown, refuted or outdated premises
and hypotheses cannot give effective supported status. answer_needs_refresh
requests a new synthesis when its linked claims change; old versions remain.

Give useful conditional answers and next steps. Save claims, conditions and
gaps. Audit givens against user wording. Readiness describes recorded work
and remaining checks, never factual or legal correctness.
