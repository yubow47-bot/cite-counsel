# McGill Citation Tool — API Contract

Frontend-agnostic HTTP API.  Consumed by Next.js web frontend (v0-generated)
and future Chrome Extension.

## Base URL

Dev: `http://localhost:8000`  
Prod: configured at deploy time (HF Spaces / Render / Railway)

## Global conventions

### Response envelope

Every endpoint returns a uniform JSON object:

```json
{
  "ok": true,
  "route": "case_name | citation_number | legislation | constitutional_statutes | concept | journal | bill | select | file | url | chat | by_law | case | treaty | foreign | general_rules",
  "status": "done | needs_selection | needs_input | unsupported | error",
  "data": {},
  "debug": null | {},
  "error": null | { "reason": "human-readable message" }
}
```

| field | meaning |
|---|---|
| `ok` | `true` for any handled outcome (including unsupported); `false` only for system errors |
| `route` | the classification/processing path the backend chose |
| `status` | `done` = result ready; `needs_selection` = multiple A2AJ candidates shown; `needs_input` = A2AJ grounding failed, show scaffold form; `unsupported` = genuinely no McGill path (Hansard etc.); `error` = system/validation failure |
| `data` | endpoint-specific payload |
| `debug` | `null` in production; rich trace in dev (see DEBUG_RESPONSES below) |
| `error` | `null` except when `status="error"`; `reason` is user-facing Chinese text |

### Status → HTTP status code mapping

| status | HTTP | notes |
|---|---|---|
| `done` | 200 | |
| `needs_selection` | 200 | multiple A2AJ candidates shown |
| `needs_input` | 200 | A2AJ grounding failed; show scaffold form |
| `unsupported` | 200 | genuinely no scaffold (Hansard etc.) |
| `error` | 4xx / 5xx | validation or system failure |

### Debug control (IP protection)

**Environment variable `DEBUG_RESPONSES`** (default `false`):

- `false` (production): `debug` is **always `null`** in every response.  Never exposed
  to the client.  Server-side logging still works.
- `true` (local dev): `debug` contains `{route, a2aj_summary, deepseek_prompt,
  deepseek_response, rule_used}`.  The prompt and rules are the McGill rule
  database — core IP — so this is **never** enabled in production.

### CORS

Controlled by `ALLOWED_ORIGINS` (env, comma-separated, default `http://localhost:3000`).

---

## Endpoints

### 1. POST `/api/citation` — Citation query (hero flow)

Search and format a legal citation.

**Request:**
```json
{ "input": "R v Oakes" }
```

**Response** — single result (`status="done"`):
```json
{
  "ok": true,
  "route": "citation_number",
  "status": "done",
  "data": {
    "citations": [
      { "citation": "<格式化的 McGill 引用串>" }
    ]
  },
  "debug": null,
  "error": null
}
```

**Response** — concept (multi-result):
```json
{
  "ok": true,
  "route": "concept",
  "status": "done",
  "data": {
    "citations": [
      { "citation": "...", "source_case": "Haida Nation v British Columbia" },
      { "citation": "...", "source_case": "Criminal Code, RSC 1985, c C-46, s 35" }
    ]
  }
}
```

**Response** — multiple candidates (`status="needs_selection"`):
```json
{
  "ok": true,
  "route": "case_name",
  "status": "needs_selection",
  "data": {
    "candidates": [
      {
        "display": "✅ R v Oakes — [1986] 1 SCR 103",
        "style_of_cause": "R v Oakes",
        "neutral_citation": "[1986] 1 SCR 103",
        "reporter": "(1986) 53 OR (2d) 97",
        "year": "1986",
        "date": "1986-02-28",
        "url": "https://canlii.ca/t/1ft6f",
        "verified": true
      }
    ]
  }
}
```

Each candidate carries the full structured fields that `format_citation` needs
(not just the display string).  Frontend stores the array and passes it back
to `/api/citation/select`.

**Response** — grounding failed, show scaffold (`status="needs_input"`):

Emitted when A2AJ / LEGISinfo returns zero verified results but a scaffold
template exists for the query type.  Frontend switches to manual fill form
using config from `GET /api/scaffold/config`.

```json
{
  "ok": true,
  "route": "case_name",
  "status": "needs_input",
  "data": {
    "message": "Could not verify against our databases. Fill in the fields below to generate a McGill 10th citation.",
    "prefill": { "style_of_cause": "NonExistent v Nobody 2024", "year": "2024" },
    "suggested_type": "jurisprudence"
  },
  "error": null
}
```

`prefill` is best-effort extraction from the raw query (year regex +
route-based field assignment).  No LLM, no heuristics.  May be empty `{}`.

`suggested_type` maps the classifier route to a scaffold type key (from
`GET /api/scaffold/config`).  Frontend uses it to preselect the dropdown;
user may switch to any other type.

**Response** — unsupported:
```json
{
  "ok": true,
  "route": "case_name",
  "status": "unsupported",
  "data": {},
  "error": { "reason": "未找到匹配结果，请尝试其他关键词。" }
}
```

---

### 2. POST `/api/citation/select` — Candidate selection (stateless)

Format a previously-returned candidate.  No server state — frontend passes the
same candidates array back.

**Request:**
```json
{
  "candidates": [ /* same array from /api/citation response */ ],
  "selected_index": 0
}
```

**Response:**
```json
{
  "ok": true,
  "route": "select",
  "status": "done",
  "data": {
    "citations": [{ "citation": "<McGill 引用>" }]
  }
}
```

---

### 3. POST `/api/extract/file` — File upload & citation extract

**Request:** `multipart/form-data`, field name `file`.  
Accepted: `.docx`, `.pdf`, `.pptx`, `.xlsx`.  
Max size: `MAX_UPLOAD_MB` (env, default 10 MB).  Exceeding returns
`status="error"`.

**Response:**
```json
{
  "ok": true,
  "route": "file",
  "status": "done",
  "data": {
    "citations": [{ "citation": "<McGill 引用>" }],
    "doc_type": "journal_article | book | report | ..."
  }
}
```

---

### 4. POST `/api/extract/url` — URL citation extract

**Request:**
```json
{ "url": "https://..." }
```

**Response:** same shape as `/api/extract/file` (minus `doc_type`).

---

### 5. POST `/api/chat` — Free-form chat (non-streaming)

**Request:**
```json
{
  "messages": [
    { "role": "user", "content": "Explain the Gladue principle" }
  ]
}
```

**Response:**
```json
{
  "ok": true,
  "route": "chat",
  "status": "done",
  "data": { "reply": "The Gladue principle..." }
}
```

SSE streaming deferred to later iteration.

---

### 6. POST `/api/feedback` — User feedback (up / down)

**Request:**
```json
{
  "input": "R v Oakes",
  "output": "*R v Oakes*, [1986] 1 SCR 103.",
  "route": "citation_number",
  "verdict": "up",
  "note": "optional comment"
}
```

**Response:** `status="done"` with empty `data`.

Payload saved to `data/feedback.jsonl` for later upload to HF Dataset.

---

### 7. GET `/api/health` — Health check

**Response:** `{ "ok": true }`

Used by HF Spaces anti-sleep ping.

---

### 8. GET `/api/scaffold/config` — Scaffold field definitions

Returns all citation types that support manual fill-in scaffolding and their
field configurations.  **Dynamically generated from `mcgill_rules.json`** at
call time — top-level keys with a `fields` array become type options.
Clients may cache; config changes only when the rules JSON is updated.

**Response:**
```json
{
  "type_options": [
    { "type": "by_law", "label": "Municipal By-law" },
    { "type": "case", "label": "Court Case (Jurisprudence)" },
    { "type": "legislation", "label": "Legislation / Statute" },
    { "type": "treaty", "label": "Treaty / International Agreement" },
    { "type": "foreign", "label": "Foreign Law" },
    { "type": "bill", "label": "Bill (Parliamentary)" }
  ],
  "field_configs": {
    "by_law": {
      "template": "[name], By-law No [number] ([year]).",
      "fields": [
        { "key": "name", "label": "By-law Name", "placeholder": "e.g. Noise Control By-law", "required": true },
        { "key": "number", "label": "By-law Number", "placeholder": "e.g. 2024-123", "required": true },
        { "key": "year", "label": "Year", "placeholder": "YYYY", "required": true }
      ]
    }
  }
}
```

Templates marked with `TODO: verify against McGill 10th` are placeholders —
verify format before production use.

---

### 9. POST `/api/citation/assemble` — Manual scaffold assembly

Deterministic template fill from user-supplied fields.  No LLM, no database
lookup.  Result is always `verified: false`.

**Request:**
```json
{
  "type": "by_law",
  "fields": { "name": "Noise Control By-law", "number": "2024-123", "year": "2024" }
}
```

**Response:**
```json
{
  "ok": true,
  "route": "by_law",
  "status": "done",
  "data": {
    "citations": [{ "citation": "Noise Control By-law, By-law No 2024-123 (2024).", "verified": false }]
  }
}
```

---

## Environment variables

| variable | default | description |
|---|---|---|
| `DEBUG_RESPONSES` | `false` | expose debug payload in responses (dev only) |
| `MAX_UPLOAD_MB` | `10` | max uploaded file size |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | CORS allowed origins, comma-separated |
| `RATE_LIMIT_PER_MINUTE` | `30` | max requests/IP/minute |
| `DEEPSEEK_DAILY_LIMIT` | `500` | max DeepSeek API calls/day |
| `DEEPSEEK_API_KEY` | — | DeepSeek API key (already in `.env`) |
| `LLM_DEFAULT_MODEL` | `deepseek-v4-flash` | default model for classification/formatting |
| `LLM_CONCEPT_MODEL` | `deepseek-v4-pro` | model for concept expansion |

## Backend internals (not exposed)

- Model routing (flash default, pro for `expand_concept`) is an implementation
  detail of `deepseek_api.py` via env variables.  API does not expose model
  selection.
- `selection_token` / server-side session **not used** — selection is fully
  stateless via frontend-passed `candidates` array.

## Deployment notes

- **Frontend**: Next.js → Vercel (free tier)
- **Backend**: FastAPI → HF Spaces / Render / Railway
- Latency across Pacific (A2AJ in Canada, DeepSeek in China) — measure after
  deployment with real traffic, don't trust dev-machine numbers.
- Gradio (`app.py`) and FastAPI coexist; Gradio keeps running at `:7860` for
  manual testing of file / URL / chat tabs.
