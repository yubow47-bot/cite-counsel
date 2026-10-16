# Offline workflow smoke suite

The question-driven workflow also has a small opt-in **real-model capability probe**, separate from this scripted smoke suite. Run `python -m eval.inquiry_eval --live --task bill-status`, `--task policy-causality`, or `--task workplace-scope`. It uses isolated sessions, bounded model calls and public sources, saves actual answers and receipts, and leaves qualitative grading pending a separate manual review. Timings, model/review requests and tool errors are recorded separately. See [the inquiry workflow](../docs/INQUIRY_WORKFLOW.md) and [the actual trial results](INQUIRY_RESULTS_2026-10-15.md).

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m eval.runner
```

The default output is `.chatbox-runtime/eval-smoke/<UTC timestamp>/index.html`.
`--task source-binding` selects one task; `--output PATH` selects a destination.
Every run uses fresh sessions and writes `config.json`, `transcript.jsonl`,
`metrics.json`, `output/`, `scores.json`, and `report.html` per task.

This is a small **scripted, offline integration regression suite**. It does not
call a paid model or a model judge, and its pass rate says nothing about model
reasoning, legal correctness, citation coverage, or research completeness. All
matter documents and user review clicks are synthetic test fixtures.

The four workflows cover work identity, complete uploaded document retention,
source-change propagation, and restart after a premise revision. Each passes
through `Harness.run_turn`, progressive tool loading, parameter validation,
contract auditing, and the actual session store. Deterministic graders inspect
saved deliverables and resulting state, rather than the scripted completion
message. Missing files and grading exceptions cannot pass. The long-document
workflow builds a DOCX with decisive evidence in its middle and exercises the
real file extractor and bounded record reader.

The suite deliberately borrows Harvey Labs' task-directory model (`task.json`
plus `documents/`), deliverable-scoped criteria, run metadata/transcript files,
and all-criteria-pass summary from its public
[architecture](https://github.com/harveyai/harvey-labs/blob/main/docs/architecture.md),
[runner](https://github.com/harveyai/harvey-labs/blob/main/lab_core/harness/run.py),
and [scoring implementation](https://github.com/harveyai/harvey-labs/blob/main/lab_core/evaluation/scoring.py).
This implementation is independent and has not run Harvey's benchmark corpus.
Harvey's model/LLM-judge phases are outside this initial smoke suite.

[Anthropic's agent evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
informs the distinction between regression and capability tests, preserving
transcripts, and grading environment outcomes. A later, explicitly invoked
model evaluation can reuse these task instructions and deliverables while
adding repeated trials and calibrated human/model grading for legal meaning.
