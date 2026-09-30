# Cite Counsel Harness

A local agent harness for legal research and citation. The model chooses when to read an optional Skill, load a tool plugin, call a tool, or answer. The harness enforces access, resource and provenance contracts. The first deterministic formatting capability is McGill Guide (10th edition) citation rendering.

> Experimental research aid. A citation it produces is not guaranteed to be correct. Check each one against the original source and the official McGill Guide.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_chatbox.py      # opens http://127.0.0.1:8001
```

One Python process serves both the page and the API.

## The problem it addresses

Legal work needs the model to read and judge material while keeping source identity honest. The current architecture separates three responsibilities:

1. The model may read, understand and extract facts from material. Source records carry provenance (`rec_N`); tools receive the stored object by reference. `record__read` exposes bounded text slices without changing the record's origin.
2. "Verified" comes from the derivation chain. A citation is rendered from record fields, and each field it uses is a leaf in a derivation chain. It counts as verified only if every leaf comes from a database (`database`) and carries a `source_id`. A single `user`, `extracted`, or `model` leaf marks it unverified.
3. The reply check annotates text and hides nothing. Years, case citations, DOIs, and pinpoints are matched against session text. A match is a traceability hint, not proof that the claim is correct or that the source is authoritative. A miss is tagged `unsourced`.

There are five origins: `database` (returned by a database), `extracted` (read from a file or web page), `user` (the user's own words), `computed` (derived by code), and `model` (written by the model, with nothing in the session to check it against).

The model may tackle research, comparison and drafting with an explicit account of evidence and uncertainty. Deterministic tools handle citation formatting, quotation comparison and date arithmetic; those tools do not decide the legal answer for the model.

## Architecture

```text
user message + attachments
      │
      ▼
 one shared loop: general principles + tool and Skill directories
      │
      ▼
 model chooses read_skill / load_plugin / tool calls / reply
      │
      ▼
 _execute: validate params ──► handler(ctx, params)
                                 ├─ ctx.records.get(ref)   fetch a record by number
                                 ├─ ctx.save(obj, meta)    store it (category contract first)
                                 └─ Result(content=refs + summary, blocks=UI cards)
      │
      ▼
 content goes into the message history; blocks go to the UI
      │
      ▼
 reply check: annotate each fact with its source
```

| Module | Role |
| --- | --- |
| `harness/core.py` | Shared streamed agent loop, tool execution, parameter checks, built-in record tools |
| `harness/skills.py` and `skills/` | Optional Markdown methods loaded by name, independently of tools |
| `harness/plugin.py` | Plugin interface and discovery: `Plugin`, `Tool`, `Result`, `Setting`, `UserText` |
| `harness/records.py` | Session record store: numbering, category contract checks, leaf reconciliation |
| `harness/session.py` | Sessions, attachments, local persistence, append-only model/tool events, source snapshots |
| `harness/grounding.py` | Fact extraction and source annotation for replies |
| `harness/llm.py` | OpenRouter-compatible calls, streaming and model response profiles |
| `harness/app.py` | Local HTTP surface, upload checks, settings API; `harness/rate_limiter.py` limits requests |
| `harness/web/` | Single-page UI: chat box, settings bar, plugin UI loader |
| `core/tool_contracts.py` | Contract types: `Field`, `Record`, `Artifact`, `Finding`, `Derivation` |

### Plugin categories and the contract

Plugins fall into three categories. `Store.check_output` checks every save and raises `ContractError` on a violation.

| Category | May produce | Examples |
| --- | --- | --- |
| `source` | Records whose fields are all `database` and carry a real `source_id` | a2aj, legisinfo, crossref, openlibrary |
| `extract` | Records whose fields are all `extracted` | file, web |
| `function` | Artifacts and Findings, with verification computed from the derivation chain | mcgill, quote, bibliography, deadlines |

When an Artifact is saved, each `database` leaf must match an entry with the same `(value, source_id)` in the session's evidence store. A function plugin therefore cannot forge a verified leaf. Values the model fills in never enter the evidence store, so they cannot pick up a source later.

To assemble a record itself, the model uses the built-in `record__compose`. Each field is given as `{value, source, quote}`. The harness checks that the quote appears in the cited source and the value appears in the quote. If both hold, the field inherits that source's origin. If not, it is written anyway and tagged `model`. A field with a bad format (too long, a name that does not belong to the type, the wrong shape) is not written, and the reason is reported at once.

Plugins are installed, trusted code and do not run in a sandbox. The contract catches category misuse and forged provenance. See [docs/HARNESS.md](docs/HARNESS.md) for details.

### Built-in plugins

| Plugin | Category | Default | Tools | Source or implementation |
| --- | --- | --- | --- | --- |
| `a2aj` | source | on | `find_case`, `find_legislation`, `full_text` | A2AJ cases and legislation |
| `legisinfo` | source | on | `bill`, `bills` | Federal bills (LEGISinfo) |
| `crossref` | source | on | `doi`, `article` | Crossref |
| `openlibrary` | source | on | `isbn`, `book` | Open Library |
| `file` | extract | on | `extract` | PDF, DOCX, PPTX, XLSX, images |
| `web` | extract | off | `search`, `fetch` | Exa search (or DuckDuckGo Lite); page fetching with SSRF protection |
| `mcgill` | function | on | `cite`, `missing` | Renders citations from `mcgill_rules.json` and lists missing fields |
| `quote` | function | on | `check` | Checks a quotation against a stored judgment's full text and finds the paragraph |
| `bibliography` | function | on | `build` | Builds a bibliography from stored citations and recomputes verification |

### Writing a plugin

Export `PLUGIN` (a `harness.plugin.Plugin`) from `plugins/<name>/__init__.py`, or publish it through the `citecounsel.plugins` entry point. A plugin declares:

- `tools`: what the model can call after loading the plugin, each with a pydantic parameter model and a `handler(ctx, params) -> Result`.
- `actions`: deterministic operations triggered by buttons in the plugin's own UI, without going through the model.
- `ui`: an optional directory with `ui.js` and `ui.css` that renders the plugin's result cards or panel.
- `settings`: options shown in the settings bar.
- `category`: `source`, `extract`, or `function`, which decides the origins it may produce.
- `fact_patterns`: regexes for the fact shapes in the plugin's domain, used by the reply check.

A parameter that should be the user's own words is declared as `UserText`. The harness replaces it with the verified slice of the user's message before the handler runs. Plugins are discovered at startup and are not hot-reloaded.

### Sessions and persistence

Each session has a snapshot file, `sessions/<id>.json`, an append-only `sessions/<id>.events.jsonl`, and a directory for attachments and saved web responses. Snapshot writes are atomic. After a restart, record numbers continue and old references still resolve. Only a hash of the token is stored. Sessions expire after 4 hours idle, and startup sweeps expired files and caps the total.

## Usage and configuration

- Settings bar: model ID, OpenRouter API key (written to `.env` in the project root, effective immediately, never shown again), plugin switches, and each plugin's own settings. Enabling a plugin only makes it available to the model. It does not run on its own.
- Config file: copy `config/chatbox.example.json` to `config/chatbox.local.json` to change the port, the model, the vision model, the upload limit (50 MB by default), and the daily spend threshold. Keep secrets in `.env` only (see `.env.example`).
- Web search: pick a search service in the settings bar. Exa needs `EXA_API_KEY`, and a value entered in the settings bar takes precedence over `.env`. Creating a record has no mandatory web-search prerequisite.
- Launcher: `./start-chatbox.ps1`. With `-Restart` it stops only a process whose command line matches this project's `run_chatbox.py`.

### Limits and privacy

- The server listens on `127.0.0.1` only. It applies a TrustedHost check, a same-origin check, rate limiting, and a CSP. Uploads are checked by extension, size, and file header.
- Once a key is configured, conversation content is sent to OpenRouter and its model providers. Plugins also contact external databases and websites. Do not submit material that must not leave your machine.
- `daily_spend_cap_usd` is an in-process estimate that resets on restart. It is not a hard billing limit. For a strict limit, set one on the key in OpenRouter.
- A database field carries the database source identity. It does not establish that a hit matches the user's target or that the legal analysis is correct.

## Layout

```text
harness/        agent loop, plugin interface, record store, sessions, reply check, HTTP surface and page
skills/         optional on-demand method notes
plugins/        built-in plugins
core/           contract types, McGill formatting and rules, quote checking, bibliography, spend tracking
local_tools/    database adapters, URL guard, page reading (web_extract), file extraction
llm_api/        OpenRouter client and image extraction
mcgill_rules.json   McGill rules and templates (the single source for formatting)
docs/           HARNESS.md (engineering), LEGAL_TOOL_PLATFORM_BLUEPRINT.md (design spec), CHATBOX_QUICKSTART.md
tests/          unit and contract tests
profiling/      HTTP call timing helper
```

## Tests

```powershell
python -m pytest        # collects tests/ only
```

The contract tests cover plugin boundaries, Skill independence, tool execution, event recording, bounded record reading, source snapshots, source provenance and persistence. Most tests mock external services, so a pass does not show that an upstream API, credential, or model is available right now.

## Roadmap

- MCP integration: external MCP servers as plugins, and plugins exposed as MCP servers.
- A fuller UI for reply annotations: source links, excerpts, highlighting, and a prominent "unsourced" marker.

## License and notices

This project does not include or replace the McGill Guide. The Guide is a separate, copyrighted publication and the authority for its own rules. The code is released under the [MIT License](LICENSE).
