# Cite Counsel Harness

A local agent harness for Canadian legal citation. You talk to it, and the model decides which plugins to load and which tools to call. The harness makes sure every fact the model states can be traced to a source. The first use case is producing citations in the style of the *Canadian Guide to Uniform Legal Citation* (McGill Guide, 10th edition).

> Experimental research aid. A citation it produces is not guaranteed to be correct. Check each one against the original source and the official McGill Guide.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_chatbox.py      # opens http://127.0.0.1:8001
```

One Python process serves both the page and the API.

## The problem it addresses

Most "LLM plus search" tools treat sources as a soft hint. The model reads some material, writes a citation in its own words, and the user cannot tell which fields were looked up and which were invented. The harness constrains this with three rules:

1. The model never handles facts. A data source returns a record with provenance (`rec_N`). The model sees only the number and a short summary. The object stays in the session store, tools fetch it by number, and the model cannot rewrite it and pass it back in.
2. "Verified" comes from the derivation chain. A citation is rendered from record fields, and each field it uses is a leaf in a derivation chain. It counts as verified only if every leaf comes from a database (`database`) and carries a `source_id`. A single `user`, `extracted`, or `model` leaf marks it unverified.
3. The reply check annotates the text and hides nothing. Years, case citations, DOIs, and pinpoints in each paragraph of model text are matched against the session's evidence. A match gets its source and the quoted passage. A miss is tagged `unsourced`. The text is shown either way.

There are five origins: `database` (returned by a database), `extracted` (read from a file or web page), `user` (the user's own words), `computed` (derived by code), and `model` (written by the model, with nothing in the session to check it against).

The project skips work that cannot be verified, such as contract review and legal Q&A. It covers work with a right answer that code can check: citation format, source verification, quotations against the original, and deadline calculation.

## Architecture

```text
user message + attachments
      │
      ▼
 run_turn: system prompt (plugin catalogue + disabled plugins + input hints)
      │
      ▼
 model chooses load_plugin / tool calls (up to 20 steps per turn)
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
| `harness/core.py` | Agent loop, tool execution, parameter checks, built-in `record__compose` |
| `harness/plugin.py` | Plugin interface and discovery: `Plugin`, `Tool`, `Result`, `Setting`, `UserText` |
| `harness/records.py` | Session record store: numbering, category contract checks, leaf reconciliation |
| `harness/session.py` | Sessions, message history, attachments, local persistence |
| `harness/grounding.py` | Fact extraction and source annotation for replies |
| `harness/llm.py` | OpenRouter-compatible calls with native tool calling and streaming |
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

Each session is one file, `sessions/<id>.json`, with attachments under `sessions/<id>/attachments/`. Writes are atomic. After a restart, record numbers continue and old references still resolve. Only a hash of the token is stored. Sessions expire after 4 hours idle, and startup sweeps expired files and caps the total.

## Usage and configuration

- Settings bar: model ID, OpenRouter API key (written to `.env` in the project root, effective immediately, never shown again), plugin switches, and each plugin's own settings. Enabling a plugin only makes it available to the model. It does not run on its own.
- Config file: copy `config/chatbox.example.json` to `config/chatbox.local.json` to change the port, the model, the vision model, the upload limit (50 MB by default), and the daily spend threshold. Keep secrets in `.env` only (see `.env.example`).
- Web search: pick a search service in the settings bar. Exa needs `EXA_API_KEY`, and a value entered in the settings bar takes precedence over `.env`. While the web plugin is enabled, creating a new record requires a `web__search` call earlier in the session.
- Launcher: `./start-chatbox.ps1`. With `-Restart` it stops only a process whose command line matches this project's `run_chatbox.py`.

### Limits and privacy

- The server listens on `127.0.0.1` only. It applies a TrustedHost check, a same-origin check, rate limiting, and a CSP. Uploads are checked by extension, size, and file header.
- Once a key is configured, conversation content is sent to OpenRouter and its model providers. Plugins also contact external databases and websites. Do not submit material that must not leave your machine.
- `daily_spend_cap_usd` is an in-process estimate that resets on restart. It is not a hard billing limit. For a strict limit, set one on the key in OpenRouter.
- A database hit verifies the source metadata. It does not verify the final formatting, and coverage depends on the upstream service.

## Layout

```text
harness/        agent loop, plugin interface, record store, sessions, reply check, HTTP surface and page
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

The contract tests cover out-of-category origins, unknown or cross-session record numbers, forged `database` leaves, model values staying out of the evidence store, the evidence and format checks in `record__compose`, and persistence round trips (`tests/test_harness.py`, `tests/test_tool_contracts.py`, `tests/test_persistence.py`, and others). Most tests mock external services, so a pass does not show that an upstream API, credential, or model is available right now.

## Roadmap

- MCP integration: external MCP servers as plugins, and plugins exposed as MCP servers.
- A fuller UI for reply annotations: source links, excerpts, highlighting, and a prominent "unsourced" marker.

## License and notices

This project does not include or replace the McGill Guide. The Guide is a separate, copyrighted publication and the authority for its own rules. The code is released under the [MIT License](LICENSE).
