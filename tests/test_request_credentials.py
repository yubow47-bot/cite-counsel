"""A per-request key wins over the process-level environment, and resets cleanly."""

import asyncio

import pytest
from starlette.concurrency import run_in_threadpool

from llm_api import request_credentials as creds


def test_absent_by_default_falls_back_to_environment(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert creds.openrouter_key() == ""


def test_per_request_key_wins_over_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    with creds.use_credentials(creds.RequestCredentials(openrouter_api_key="request-key")):
        assert creds.openrouter_key() == "request-key"
    # The environment key is back in control once the block exits.
    assert creds.openrouter_key() == "env-key"


def test_llm_api_key_falls_back_when_openrouter_key_absent(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "legacy-env-key")
    assert creds.openrouter_key() == "legacy-env-key"


def test_none_and_empty_credentials_are_a_no_op(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    with creds.use_credentials(None):
        assert creds.openrouter_key() == "env-key"
    with creds.use_credentials(creds.RequestCredentials()):
        assert creds.openrouter_key() == "env-key"


def test_credentials_reset_even_if_the_block_raises(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    with pytest.raises(RuntimeError):
        with creds.use_credentials(creds.RequestCredentials(openrouter_api_key="request-key")):
            assert creds.openrouter_key() == "request-key"
            raise RuntimeError("boom")
    assert creds.openrouter_key() == "env-key"


def test_a_blank_or_oversized_key_is_rejected():
    with pytest.raises(ValueError):
        creds.RequestCredentials(openrouter_api_key="   ")
    with pytest.raises(ValueError):
        creds.RequestCredentials(openrouter_api_key="x" * 401)


def test_nesting_restores_the_outer_value(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    with creds.use_credentials(creds.RequestCredentials(openrouter_api_key="outer")):
        assert creds.openrouter_key() == "outer"
        with creds.use_credentials(creds.RequestCredentials(openrouter_api_key="inner")):
            assert creds.openrouter_key() == "inner"
        assert creds.openrouter_key() == "outer"
    assert creds.openrouter_key() == "env-key"


def test_context_propagates_into_run_in_threadpool(monkeypatch):
    """The mechanism this whole module exists for: a key set on the request's
    asyncio task must still be visible inside the worker thread that
    run_in_threadpool spawns for the actual (blocking) provider call."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def read_from_worker_thread() -> str:
        return creds.openrouter_key()

    async def handle_one_request() -> str:
        with creds.use_credentials(creds.RequestCredentials(openrouter_api_key="from-the-request")):
            return await run_in_threadpool(read_from_worker_thread)

    assert asyncio.run(handle_one_request()) == "from-the-request"


def test_concurrent_requests_do_not_see_each_other_s_key(monkeypatch):
    """Two 'requests' running as concurrent asyncio tasks must not leak keys."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    async def handle(key: str) -> str:
        with creds.use_credentials(creds.RequestCredentials(openrouter_api_key=key)):
            await asyncio.sleep(0)  # yield, so the two tasks actually interleave
            return await run_in_threadpool(creds.openrouter_key)

    async def go():
        return await asyncio.gather(handle("key-a"), handle("key-b"))

    assert asyncio.run(go()) == ["key-a", "key-b"]
