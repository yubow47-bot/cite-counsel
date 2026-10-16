# Cite Counsel Harness

[![CI](https://github.com/yubow47-bot/cite-counsel/actions/workflows/ci.yml/badge.svg)](https://github.com/yubow47-bot/cite-counsel/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An agent harness for investigating legal and social questions. It preserves the user's problem, critical unknowns, actual investigation executions and argument assessments, then helps the model form an answer with explicit conditions and remaining gaps. Existing source, extraction, research and citation plugins work through the same evidence store.

The default `inquiry` plugin organizes a question, records evidence-seeking work using harness-issued execution receipts, checks saved research arguments and preserves the answer. The model chooses the path; this is not a mandatory fixed pipeline. Recorded-work readiness describes the saved investigation, not a guarantee of factual or legal correctness. See [the inquiry workflow and architecture](docs/INQUIRY_WORKFLOW.md).

## Harness architecture

```mermaid
flowchart TB
    USER["User · Chat UI"] --> H

    subgraph CORE["Core loop"]
        direction LR
        H["Harness<br/>Organizes context and controls execution"] --> M["Model<br/>Decides the next step"]
        M -->|"Tool call"| T["Execution layer and plugin tools<br/>Check parameters and execute"]
        T -->|"Result"| H
    end

    subgraph SUPPORT["Supporting modules"]
        direction LR
        S["Skills and plugin directory<br/>Methods · tool definitions"]
        R["Record and evidence store<br/>Origins · contracts · derivations"]
        L["Session persistence<br/>Model and tool event logs"]
    end

    H --- S
    T <--> R
    H --> L
    M -->|"Final reply"| G["Grounding<br/>Matches recognized fact shapes<br/>and annotates sources"]
    R -->|"Source evidence"| G
    G --> OUT["User reviews reply and source annotations"]
    T -->|"Result cards"| OUT
```

1. The model decides each next step.
2. A shared loop checks each tool call against its schema before running it.
3. The result is saved with its source, and the model works from that saved record.
4. Before a reply is shown, the harness compares its citations, years, DOIs and pinpoints with the retrieved records, tool results and the user's messages. It shows where each one came from, or marks it as unsourced when it finds no match.

At each step the model can read a Skill (a short Markdown guide to a method), ask the harness to load an enabled plugin's tools, call a tool, or reply. Reading a Skill runs nothing, and an enabled plugin runs only when the model calls one of its tools.

Results are stored once and passed around by reference. Tools read the stored records directly, so the model doesn't have to copy their details into later calls. The model sees a short summary of each result, and the user sees it as a card in the chat. The model can keep investigating after a tool has produced something.

Plugins handle the repeatable work, such as formatting citations, comparing quotations, building bibliographies and counting dates. Whether a value is verified depends on the records it was built from, and that is checked separately from formatting. The researcher still reads the retrieved material and decides whether to rely on an authority.

The research workspace also saves exact source passages, premises, claims and drafts. A passage stays bound to its original record version and character range. If a source or premise is revised, dependent claims and drafts show that they need review. Support relationships and legal conclusions remain assertions; a human review records acknowledgement of a snapshot, not legal correctness. See [the research workspace guide](docs/RESEARCH_WORKSPACE.md).

Saved conversations, result cards and materials survive reloads and plugin changes. Snapshots are kept in a local SQLite file and each save writes only what changed; snapshots from earlier JSON-file versions are imported once and the files kept. The Run log shows, per turn, every model request, tool call, review and delivery with timings and the remaining budget. New chat keeps earlier work available in the local conversation selector; deletion is explicit. Creating an inquiry retains that investigation and its materials until explicit deletion, including beyond the normal chat expiry and disk-count sweep. Other local chats expire after 30 days without use. Saved investigations currently belong to their conversation; cross-conversation project sharing is not implemented. The materials directory provides paged reading and literal text search, including text beyond the chat history. File extraction saves all extracted text with its coverage; scanned PDFs can be processed in successive page ranges.

## Example

Plugins run under the harness's rules. Here the Quote and McGill plugins run on a made-up judgment whose paragraph 2 reads *"The party seeking to uphold a limit must show it is demonstrably justified."*

```console
exact quotation        -> found, at para 2
lower-case "the"       -> case_differs (McGill requires exact quotations)
paraphrase             -> not_found
R v Oakes + reporter   -> *R v Oakes*, [1986] 1 SCR 103 at para 69.
R v Oakes only         -> refused: still needed: neutral or reporter citation
```

## Tools are plugins

Every tool is a plugin, and each plugin is one of three kinds. The kind sets what its output may claim as a source. If a plugin claims more, the harness downgrades the claim.

| Kind | What it does | Its output counts as |
| --- | --- | --- |
| Source | Looks up a database | Data from a named source |
| Extract | Reads a file or web page | Extracted text |
| Function | Formats, compares or calculates | Derived output with its inputs listed |

The current plugins cover legal and bibliographic databases, web search and page fetching, file extraction and McGill Guide citations. Other legal projects and methods can be added as new plugins.

| Plugin | Kind | What it does |
| --- | --- | --- |
| [A2AJ](https://law.a2aj.ca/) | source | Canadian cases and legislation |
| [LEGISinfo](https://www.parl.ca/legisinfo/en/) | source | Canadian federal bills |
| [Crossref](https://www.crossref.org/) | source | Journal articles by DOI or title |
| [Open Library](https://openlibrary.org/) | source | Books by ISBN or title |
| File | extract | Uploaded PDF, Word, PowerPoint, Excel and images |
| Web | extract | Public web pages |
| McGill | function | McGill Guide citations, with missing details listed |
| Quote | function | Checks quotations against judgment text |
| Bibliography | function | Bibliography from saved citations |
| Deadlines | function | Date calculations from given inputs |
| Research | function | Versioned excerpts, premises, claims, drafts, dependency review and source version comparison |
| Footnotes | function | Ordered case footnotes with full citation, Ibid and supra worked out from note order |

## Quick start

You need an API key.

```bash
docker build -t cite-counsel .
docker run --rm -p 127.0.0.1:8001:8001 -e OPENROUTER_API_KEY=your-key cite-counsel
```

Or with Python:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_chatbox.py
```

Open <http://127.0.0.1:8001> and enter the model and key in the settings bar.

## Privacy

Conversations are sent to the model provider, and plugins contact outside services. Chats are saved on your computer without encryption. Don't enter material that must stay on your machine.

## Development

[docs/HARNESS.md](docs/HARNESS.md) explains the design and [CONTRIBUTING.md](CONTRIBUTING.md) covers writing plugins.

Automated tests cover tool contracts, source identity, citations, extraction coverage, reply annotations, saved sessions and research dependencies. CI runs them on Python 3.11 and 3.12. The default run is offline, with outside services mocked. Run them with `pip install pytest httpx` and `pytest`.

A small workflow smoke suite follows Harvey Labs' public task/deliverable/report structure:

```powershell
.\.venv\Scripts\python.exe -m eval.runner
```

It runs four synthetic workflows through the real harness with scripted model responses and deterministic grading. Reports are saved under `.chatbox-runtime/eval-smoke/`. It checks integration regressions, not model reasoning or legal quality. [Evaluation scope and references](eval/README.md).

## License

[MIT](LICENSE). This project does not include or replace the McGill Guide, a separate copyrighted publication.
