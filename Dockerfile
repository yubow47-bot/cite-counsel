# ── McGill Citation Tool — Dockerfile (FastAPI backend) ──

FROM python:3.11-slim

# System deps for pdfplumber (pdfminer.six), pymupdf, trafilatura
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest and install
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY api/ ./api/
COPY core/ ./core/
COPY config/ ./config/
COPY local_tools/ ./local_tools/
COPY llm_api/ ./llm_api/
COPY profiling/ ./profiling/
COPY utils/ ./utils/
COPY data/ ./data/
COPY mcgill_rules.json .

# HF Spaces expects the app on port 7860
EXPOSE 7860

# uvicorn serves api.main:app on 0.0.0.0:7860
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "7860"]
