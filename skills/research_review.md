# Reviewing saved legal research
> Preserve the premises, source passages and unresolved issues behind a legal conclusion, and inspect changes before relying on saved work.

The research plugin saves assertions and exact evidence links. A literal
quotation match establishes the text and version; it does not establish who
endorsed the statement or whether it supports a legal conclusion.
For a uniquely occurring exact passage, `excerpt` can locate the quote when
both offsets are omitted. For a repeated passage, obtain explicit offsets
with `record__search`; do not guess offsets or silently choose an occurrence.

Distinguish a court's finding, a party's allegation, a dissent, a quotation
from another authority and an inference. Treat speaker labels, support
relationships and premise states as assertions requiring review. Read enough
surrounding text to assess limitations, definitions and exceptions.

Unknown facts remain unknown. A disputed premise or opposing passage remains
visible when producing a conclusion. Missing evidence cannot be repaired by
marking the assertion supported. The list of relevant premises can itself be
incomplete; do not call a saved checklist proof of exhaustive legal analysis.

Choose the basis of each saved assertion deliberately:

- `source` records an assertion whose support is claimed from linked source
  excerpts. Explain what the passage supports and what it does not establish.
- `assumption` records an explicitly unverified working premise. A user's
  account may be the information available, but it is not independently
  established by being saved. Preserve an unknown or disputed status where
  appropriate and identify what information could resolve it.
- `inference` records a conclusion drawn from saved premise references and
  a written reasoning step. It need not repeat a supporting quotation from
  each premise. Its premises, inferential step and omitted possibilities
  still need examination; the absence of a mechanical support gap is not
  proof that the reasoning is sound.

Record jurisdiction, relevant date, procedure and factual context when known.
Leave unknown scope fields empty rather than inventing precision. Explain
conditional applicability and exceptions in the scope note or limitations.
Do not transplant a rule into another setting without examining whether its
conditions hold. These scope labels are declarations, not verified findings.

For a conclusion important to the user's decision, save the reasoning, the
strongest identified counterargument or opposing evidence, the response, and
remaining limitations. Read opposing passages in context. Recording a response
does not erase contrary evidence or establish that the response succeeds.
Use `inspect` to locate unknown or disputed premises along the saved dependency
paths. A model assessment remains `model_assessed`; neither a supported label
nor a human reading acknowledgement makes a legal conclusion verified.

Saved excerpts name immutable source versions. A newer source, corrected fact
or revised premise can require dependent work to be reconsidered. Inspect the
saved dependencies before reusing a draft. A stale draft remains a historical
snapshot until revised; user acknowledgement is not a legal correctness seal.
