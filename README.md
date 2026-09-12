---
title: McGill Legal Citation Tool
emoji: 📚
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# McGill Legal Citation Tool

Backend API for the McGill Citation Generator.  
FastAPI application served via uvicorn on port 7860.

All configuration is via environment variables (HF Space Secrets).
See the GitHub repo for full documentation.

Set `CANDIDATE_SIGNING_KEY` to a long random secret when running more than one
API worker or replica. It signs candidate metadata returned by `/api/citation`
and must be identical across replicas and restarts. A single-process deployment
can omit it; the API then creates an in-memory random key at startup, so a
candidate issued before a restart must be searched for again.
