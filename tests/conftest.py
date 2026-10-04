"""Shared test setup."""

import os

import pytest


# ── Network isolation ─────────────────────────────────────────────────────────
#
# The default run is offline.  Tests that talk to a real service by design are
# marked @pytest.mark.live and skipped unless RUN_LIVE_TESTS=1.  Every other test
# runs with DNS and outbound connects blocked at the socket layer, and fails if
# it tried, so a hidden network dependency shows up as a red test instead of a
# pass that depends on the machine.  (curl_cffi goes through libcurl, below the
# socket module; code using it must be patched at the call site.)

import socket

_LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1", "testserver"}
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_RUN_LIVE = os.environ.get("RUN_LIVE_TESTS") == "1"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: talks to a real external service; skipped unless RUN_LIVE_TESTS=1")


def pytest_collection_modifyitems(config, items):
    if _RUN_LIVE:
        return
    skip = pytest.mark.skip(reason="live network test; set RUN_LIVE_TESTS=1 to run")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        yield
        return
    attempts = []

    def getaddrinfo(host, *args, **kwargs):
        if host in _LOCAL_HOSTS:
            return _real_getaddrinfo(host, *args, **kwargs)
        attempts.append(host)
        raise socket.gaierror(socket.EAI_NONAME, f"network blocked in tests: {host}")

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else None
        if not isinstance(address, tuple) or host in _LOCAL_HOSTS:
            return _real_connect(self, address)
        attempts.append(host)
        raise OSError(f"network blocked in tests: {address}")

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    yield
    if attempts:
        pytest.fail(f"test tried to reach the network ({sorted(set(attempts))}); "
                    "mock the call or mark the test @pytest.mark.live", pytrace=False)
