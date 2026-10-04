# Contributing

Issues and pull requests are welcome.

## Set up

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest httpx   # Windows: .\.venv\Scripts\pip
.venv/bin/python -m pytest
```

CI runs the same tests on Python 3.11 and 3.12 and builds the Docker image.

## Adding a plugin

1. Create `plugins/<name>/__init__.py` exporting `PLUGIN`.
2. Pick the category that matches what the tool returns: `source` (database-origin records), `extract` (content read from files or pages) or `function` (artifacts and findings with derivations). The contracts in [docs/HARNESS.md](docs/HARNESS.md) reject output that does not fit.
3. Add tests under `tests/`. Mock external services: the test run blocks the network and fails a test that tries to use it. A test that must call a real service gets `@pytest.mark.live` and runs only with `RUN_LIVE_TESTS=1`.

## Pull requests

- Keep changes focused and include a test for new behavior.
- Describe only what the code checks. README and docs keep traceability and legal correctness separate.
- Never commit API keys, `.env`, session data or confidential documents.
