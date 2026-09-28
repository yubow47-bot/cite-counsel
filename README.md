# Cite Counsel

Cite Counsel is an experimental web application for producing citations in the style of the *Canadian Guide to Uniform Legal Citation* (McGill Guide), 10th edition. It combines public metadata services, document extraction, and language-model-assisted classification and formatting.

This repository is under active development. A successful response is a research aid, not a guarantee that a citation is correct. Check every result against the source and the official McGill Guide before relying on it.

## What is implemented

The browser application exposes three user flows:

| Flow | Implemented path | Important limits |
| --- | --- | --- |
| Citation search | A query is classified, searched, and either formatted directly or returned as signed candidates for the user to choose from. Case and legislation lookups primarily use A2AJ; federal bill lookup uses LEGISinfo. | Classification and most formatting require an LLM provider. Coverage is limited by the upstream services. A database match verifies source metadata, not the final McGill formatting. |
| URL, DOI, or ISBN | DOI metadata is resolved through Crossref; ISBN metadata through Open Library. Ordinary web pages are fetched and parsed before classification and formatting. | URL extraction does not execute JavaScript and rejects or fails on many blocked, dynamic, empty, or PDF URLs. DOI/ISBN lookup depends on upstream coverage. |
| File or image | The API accepts PDF, DOCX, PPTX, XLSX, JPG/JPEG, PNG, and WebP files, extracts available content, classifies the source, and formats a citation. | Images and scanned PDFs require Gemini vision; scanned PDFs use at most the first three rendered pages. Text extraction and model output can be incomplete or wrong. The default upload limit is 10 MB. |

When a search has several verified matches, the frontend asks the user to select one before formatting. Candidate payloads are HMAC-signed by the API and are rejected if altered.

A deterministic manual form also exists, but it is disabled by default. It must be enabled in both the frontend and API, and its output is explicitly marked unverified.

### CanLII status

The repository contains a CanLII adapter and fallback branches for selected case and legislation lookups. They require `CANLII_API_KEY`, but they are experimental and are **not a supported or reliably verified product capability**. Unit tests around these branches use mocks and do not establish live API coverage. The normal documented setup does not require a CanLII key.

## How a request moves through the project

```text
Next.js frontend
  ├─ citation query ──> POST /api/citation ──> optional candidate selection
  │                                           POST /api/citation/select
  ├─ file/image ─────> POST /api/extract/file
  └─ URL/DOI/ISBN ──> POST /api/extract/url

FastAPI
  ├─ input validation, upload checks, rate limiting, and candidate signatures
  ├─ A2AJ / LEGISinfo / Crossref / Open Library lookups
  ├─ local document and web-page extraction
  └─ Gemini or an OpenAI-compatible completion endpoint for model-assisted steps
```

Other implemented API routes include health and warm-up checks, feedback collection, scaffold configuration/assembly, and a non-streaming chat endpoint. The current frontend does not expose the chat endpoint.

## Technology stack

- Frontend: Next.js 16, React 19, TypeScript 5.7, Tailwind CSS 4, Base UI, Vitest, and Testing Library.
- Backend: Python 3.11, FastAPI, Uvicorn, and Pydantic.
- Extraction: trafilatura, pdfplumber, PyMuPDF, python-docx, python-pptx, and openpyxl.
- Integrations: A2AJ, LEGISinfo, Crossref, Open Library, Gemini, and DeepSeek or another OpenAI-compatible completion service.

Key directories:

```text
frontend/       Next.js application and component tests
api/            FastAPI routes, request guards, and manual scaffold
core/           citation formatting and spend tracking
local_tools/    database adapters, URL guards, and file extraction
llm_api/        Gemini and compatible-completions clients
tests/          backend unit and contract tests
data/           static lookup data and ignored runtime records
```

## Local development

### Requirements

- Python 3.11+
- Node.js 20.9+ and npm

### Backend

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.

The health endpoint is `http://localhost:8000/api/health`.

### Frontend

In a second terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000`. Unless overridden at build time, the frontend calls `http://localhost:8000`.

## Configuration

Put backend secrets in a root `.env` file or in the deployment platform's secret store. `.env` is ignored by Git. Do not prefix private values with `NEXT_PUBLIC_`; Next.js embeds those variables in browser-delivered code.

### Core settings

| Variable | When needed | Description |
| --- | --- | --- |
| `GEMINI_API_KEY` | Search classification and image/scanned-PDF extraction | Gemini API credential. |
| `DEEPSEEK_API_KEY` | Default formatting and fallback model calls | Credential for the default DeepSeek completions endpoint. |
| `LLM_COMPLETIONS_URL` | Optional | Replaces the default DeepSeek URL with an OpenAI-compatible endpoint. |
| `OPENROUTER_API_KEY` or `LLM_API_KEY` | With a custom completions URL | Credential used for the compatible endpoint. |
| `LLM_DEFAULT_MODEL` | Optional | Model identifier for compatible completion calls; defaults to `deepseek-v4-flash`. |
| `GEMINI_TEXT_MODEL`, `GEMINI_VISION_MODEL` | Optional | Override the Gemini text and vision models. |
| `NEXT_PUBLIC_API_BASE_URL` | Frontend deployment | Public base URL of the FastAPI service. |
| `ALLOWED_ORIGINS` | Backend deployment | Comma-separated CORS origins; defaults to `http://localhost:3000`. |
| `CANDIDATE_SIGNING_KEY` | Production, especially multiple workers | High-entropy shared secret used to sign candidate payloads. Without it, each process creates an ephemeral key. |

### Operational and optional settings

| Variable | Default | Description |
| --- | --- | --- |
| `MAX_UPLOAD_MB` | `10` | Maximum accepted upload size. |
| `RATE_LIMIT_PER_MIN` / `RATE_LIMIT_PER_HOUR` | `30` / `200` | In-memory per-client request limits. |
| `DAILY_SPEND_CAP_USD` | `10` | Daily model-spend guard. |
| `DEBUG_RESPONSES` | `false` | Includes internal debug data in API responses when enabled; keep off publicly. |
| `SCAFFOLD_ENABLED` | `false` | Enables the API manual-assembly route. |
| `NEXT_PUBLIC_SCAFFOLD_ENABLED` | unset | Shows the manual form in the frontend when set to `true`; this is a public build-time flag. |
| `DISCORD_FEEDBACK_WEBHOOK` | unset | Optional feedback notification destination. |
| `NEXT_PUBLIC_GA_MEASUREMENT_ID` | unset | Optional public Google Analytics measurement ID. |

## Verification

Backend unit tests are collected only from `tests/`; scripts under `profiling/` may make live paid API calls and are intentionally excluded by `pytest.ini`.

```powershell
python -m pytest

cd frontend
npm test
npm run lint
npm run build
```

Most integration tests mock external providers. Passing the suite does not prove that an upstream API, credential, model, or deployment is currently available. Use the scripts under `scripts/` only when you intentionally want a live provider check and understand its credential and cost implications.

## Deployment

The root `Dockerfile` builds the FastAPI service only and starts it on port `7860`:

```powershell
docker build -t cite-counsel-api .
docker run --rm -p 7860:7860 --env-file .env cite-counsel-api
```

Deploy `frontend/` separately. Set `NEXT_PUBLIC_API_BASE_URL` before the frontend production build and configure the backend's CORS policy for the deployed frontend.

Before a public deployment, review these implementation details:

- API routes are unauthenticated. The included rate limiter is per-process memory, so it is not a distributed abuse-control system.
- `api/main.py` allows configured origins and also contains a `https://*.vercel.app` CORS regex. Narrow or remove that regex if previews from arbitrary Vercel subdomains should not reach the API.
- Keep `DEBUG_RESPONSES=false`. Treat uploaded documents, URLs, citation queries, and feedback as potentially sensitive user data.
- Feedback is written to `data/feedback.jsonl` and may also be sent to Discord when configured. Establish retention and disclosure policies before enabling public traffic.
- Uploads are restricted by extension, size, and file signature, and temporary files are deleted after processing. Continue to isolate parsers and keep dependencies patched.
- URL fetching uses an SSRF guard, but it should still run with restricted network permissions in production.
- Use HTTPS and a stable, random `CANDIDATE_SIGNING_KEY`; never place provider keys in frontend variables or commit `.env` files.

## API response contract

Application routes return a common JSON envelope:

```json
{
  "ok": true,
  "route": "bill",
  "status": "done",
  "data": {},
  "debug": null,
  "error": null
}
```

`status` can be `done`, `needs_selection`, `needs_input`, `unsupported`, or `error`. See `api_contract.md` for the route-level contract.

## Legal and project status

This code does not include or replace the McGill Guide. The Guide is a separate copyrighted publication and remains the authoritative source for its rules.

The code is released under the [MIT License](LICENSE).
