# Cite Counsel

**An in-development McGill Guide (10th ed.) citation tool for Canadian legal research.**

Cite Counsel helps students, researchers, and legal professionals create citations from a search, a URL/DOI/ISBN, or an uploaded document. It combines structured metadata, user-provided material, and optional external services to format results for the *Canadian Guide to Uniform Legal Citation* (McGill Guide), 10th edition.

> Citations are provided as a research aid. Always check the final result against the official McGill Guide and the source material before relying on it.

## What it does

- Searches Canadian cases, legislation, and federal bills using plain-language queries or citation details.
- Uses guided candidate selection where a lookup returns more than one plausible result.
- Creates citations from a URL, DOI, or ISBN.
- Extracts citation-relevant information from PDF, DOCX, PPTX, XLSX, JPG, PNG, and WEBP uploads.
- Provides a manual citation form for source types that cannot be verified automatically.

Coverage and external-service availability vary by source type and deployment. In particular, the repository contains experimental CanLII integration code, but it is not currently presented as a supported or reliably verified product capability.

## Architecture

| Layer | Technology | Responsibility |
| --- | --- | --- |
| Web app | Next.js 16, React 19, TypeScript, Tailwind CSS | Search, source selection, uploads, results, and feedback UI |
| API | FastAPI, Uvicorn, Pydantic | Citation endpoints, file handling, validation, rate limits, and CORS |
| Extraction | pdfplumber, PyMuPDF, python-docx, python-pptx, openpyxl, trafilatura | Read uploaded files and web content |
| External services | A2AJ, LEGISinfo, Crossref, Open Library, and optional LLM providers | Lookup, metadata, classification, and extraction support; availability varies |
| Experimental integration | CanLII adapter code | Not a documented supported capability; do not rely on it for verification |

The repository contains two independently runnable applications:

```text
frontend/       Next.js client (default: http://localhost:3000)
api/            FastAPI application
core/           Citation formatting, spend tracking, and integrations
local_tools/    Source adapters and extraction utilities
tests/          Backend test suite
```

## Run locally

### Prerequisites

- Python 3.11+
- Node.js 20+ and npm

### 1. Start the API

```bash
python -m venv .venv
.venv\\Scripts\\activate       # Windows PowerShell
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.

### 2. Start the web app

In a separate terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000`. The frontend uses `http://localhost:8000` unless `NEXT_PUBLIC_API_BASE_URL` is set.

## Configuration

Create a local `.env` file for the API. It is intentionally ignored by Git; do not commit keys, tokens, webhooks, or deployment-specific URLs.

| Variable | Required | Purpose |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | For DeepSeek features | Default compatible-completions provider |
| `GEMINI_API_KEY` | For Gemini features | Gemini extraction/classification features |
| `CANDIDATE_SIGNING_KEY` | Multi-worker production | Long, random secret used to sign candidate metadata; keep identical across replicas |
| `ALLOWED_ORIGINS` | Production | Comma-separated browser origins allowed to call the API |
| `MAX_UPLOAD_MB` | No | Upload limit; defaults to `10` |
| `RATE_LIMIT_PER_MIN` / `RATE_LIMIT_PER_HOUR` | No | Per-client API rate-limit overrides |
| `LLM_COMPLETIONS_URL`, `LLM_API_KEY`, `LLM_DEFAULT_MODEL` | No | Override the compatible LLM endpoint and model |
| `NEXT_PUBLIC_API_BASE_URL` | Frontend production | Public API base URL used at build time by Next.js |
| `NEXT_PUBLIC_GA_MEASUREMENT_ID` | No | Google Analytics measurement ID (safe to expose as a public client setting) |

Client-side variables prefixed `NEXT_PUBLIC_` are deliberately visible in browser bundles. Never put a private key or webhook URL in one.

## Verification

Run the backend tests from the repository root:

```bash
pytest
```

Run frontend checks from `frontend/`:

```bash
npm run lint
npm test
npm run build
```

## Deployment

The included `Dockerfile` packages the FastAPI service and listens on port `7860`, which is suitable for Hugging Face Spaces-style deployments:

```bash
docker build -t cite-counsel-api .
docker run --rm -p 7860:7860 --env-file .env cite-counsel-api
```

Deploy the Next.js app separately and set `NEXT_PUBLIC_API_BASE_URL` to the public HTTPS address of the API. Set `ALLOWED_ORIGINS` on the API to the exact deployed frontend origin(s), and use a persistent, high-entropy `CANDIDATE_SIGNING_KEY` whenever the API has multiple workers or replicas.

## Security notes

- Keep secrets in deployment secret stores or local `.env` files only; rotate a key immediately if it is ever committed or exposed.
- The API restricts upload extensions and checks file signatures before parsing. Keep `MAX_UPLOAD_MB` and rate limits appropriate for your deployment.
- Candidate data returned to the browser is HMAC-signed to prevent client-side tampering during the selection flow.
- Treat user-provided URLs and documents as untrusted input. Run the application behind HTTPS and configure CORS narrowly in production.

## License and guide content

This repository contains application code and does not reproduce the McGill Guide. The *Canadian Guide to Uniform Legal Citation* is a separate publication; obtain and consult the official guide for authoritative rules.
