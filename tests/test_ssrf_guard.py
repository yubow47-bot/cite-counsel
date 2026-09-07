"""Tests for the SSRF guard (local_tools/url_guard.py + fetch_html integration).

Covers: scheme allowlist, internal-address blocklist (loopback, private,
link-local / cloud metadata), credential rejection, per-hop redirect
validation, redirect-hop cap, and response-size cap.

socket.getaddrinfo is patched throughout so no test touches real DNS.

Run: pytest tests/test_ssrf_guard.py -v
"""

import ipaddress
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from local_tools.url_guard import (
    MAX_RESPONSE_BYTES,
    UrlBlocked,
    is_blocked_ip,
    next_redirect_url,
    validate_url,
)
from llm_api.deepseek_api import fetch_html

_PUBLIC_IP = "93.184.216.34"


def _fake_resolver(extra=None):
    """socket.getaddrinfo stand-in.

    IP-literal hostnames resolve to themselves; names look up ``extra``
    (falling back to example.com → a public IP).  Unknown names raise OSError,
    mirroring a resolution failure.
    """
    table = {"example.com": _PUBLIC_IP}
    table.update(extra or {})

    def fake(hostname, port=None, *args, **kwargs):
        try:
            ipaddress.ip_address(hostname)
            ips = [hostname]
        except ValueError:
            if hostname not in table:
                raise OSError(f"unresolved: {hostname}")
            ips = [table[hostname]]
        return [(2, 1, 6, "", (ip, 0)) for ip in ips]

    return fake


# ═════════════════════════════════════════════════════════════════════════════
#  validate_url — scheme + credential rules
# ═════════════════════════════════════════════════════════════════════════════

def test_validate_url_allows_public_https():
    with patch("socket.getaddrinfo", _fake_resolver()):
        assert validate_url("https://example.com/article") == "https://example.com/article"


def test_validate_url_rejects_file_scheme():
    with patch("socket.getaddrinfo") as ga:
        with pytest.raises(UrlBlocked):
            validate_url("file:///etc/passwd")
    ga.assert_not_called()


def test_validate_url_rejects_ftp_scheme():
    with pytest.raises(UrlBlocked):
        validate_url("ftp://example.com/doc")


def test_validate_url_rejects_empty():
    with pytest.raises(UrlBlocked):
        validate_url("")
    with pytest.raises(UrlBlocked):
        validate_url(None)


def test_validate_url_rejects_credentials():
    with patch("socket.getaddrinfo", _fake_resolver()):
        with pytest.raises(UrlBlocked):
            validate_url("http://user:pass@example.com/")


def test_validate_url_rejects_too_long():
    with pytest.raises(UrlBlocked):
        validate_url("https://example.com/" + "a" * 3000)


# ═════════════════════════════════════════════════════════════════════════════
#  validate_url — internal-address blocklist
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("url,host,ip", [
    ("http://127.0.0.1/", "127.0.0.1", "127.0.0.1"),
    ("http://localhost/", "localhost", "127.0.0.1"),
    ("http://[::1]/", "::1", "::1"),
    ("http://169.254.169.254/latest/meta-data/", "169.254.169.254", "169.254.169.254"),
    ("http://10.0.0.5/", "10.0.0.5", "10.0.0.5"),
    ("http://192.168.1.1/", "192.168.1.1", "192.168.1.1"),
    ("http://172.16.0.9/", "172.16.0.9", "172.16.0.9"),
    ("http://2130706433/", "2130706433", "127.0.0.1"),  # decimal-integer IP form
    ("http://0.0.0.0/", "0.0.0.0", "0.0.0.0"),
])
def test_validate_url_blocks_internal_targets(url, host, ip):
    with patch("socket.getaddrinfo", _fake_resolver({host: ip})):
        with pytest.raises(UrlBlocked):
            validate_url(url)


def test_validate_url_blocks_unresolvable_host():
    def fail(hostname, port=None, *args, **kwargs):
        raise OSError("name resolution failure")
    with patch("socket.getaddrinfo", fail):
        with pytest.raises(UrlBlocked):
            validate_url("https://no-such-host.invalid/")


def test_is_blocked_ip_unparseable_fails_closed():
    assert is_blocked_ip("not-an-ip") is True


# ═════════════════════════════════════════════════════════════════════════════
#  next_redirect_url
# ═════════════════════════════════════════════════════════════════════════════

def test_next_redirect_follows_same_site():
    with patch("socket.getaddrinfo", _fake_resolver()):
        assert next_redirect_url("https://example.com/a", "/b") == "https://example.com/b"


def test_next_redirect_blocks_internal_hop():
    with patch("socket.getaddrinfo", _fake_resolver()):
        with pytest.raises(UrlBlocked):
            next_redirect_url("https://example.com/a", "http://169.254.169.254/latest/meta-data/")


def test_next_redirect_blocks_missing_location():
    with pytest.raises(UrlBlocked):
        next_redirect_url("https://example.com/a", "")


# ═════════════════════════════════════════════════════════════════════════════
#  fetch_html — SSRF integration (curl_cffi primary path)
# ═════════════════════════════════════════════════════════════════════════════

def _cffi_resp(status=200, text="", headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.headers = headers or {}
    return resp


def test_fetch_html_blocks_file_scheme_without_any_request():
    with patch("curl_cffi.requests.get") as mock_get, \
         patch("llm_api.deepseek_api.request_with_retry") as mock_fb:
        assert fetch_html("file:///etc/passwd") is None
    mock_get.assert_not_called()
    mock_fb.assert_not_called()


def test_fetch_html_blocks_loopback_target():
    with patch("socket.getaddrinfo", _fake_resolver()), \
         patch("curl_cffi.requests.get") as mock_get, \
         patch("llm_api.deepseek_api.request_with_retry") as mock_fb:
        assert fetch_html("http://127.0.0.1:8080/admin") is None
    mock_get.assert_not_called()
    mock_fb.assert_not_called()


def test_fetch_html_follows_redirect_to_public_target():
    with patch("socket.getaddrinfo", _fake_resolver()), \
         patch("curl_cffi.requests.get", side_effect=[
             _cffi_resp(302, headers={"location": "https://example.com/real"}),
             _cffi_resp(200, text="<html>final</html>"),
         ]) as mock_get, \
         patch("llm_api.deepseek_api.request_with_retry") as mock_fb:
        html = fetch_html("https://example.com/start")

    assert html == "<html>final</html>"
    assert mock_get.call_count == 2
    mock_fb.assert_not_called()


def test_fetch_html_blocks_redirect_to_metadata_ip():
    """External page 302s into cloud metadata — must be refused, one request only."""
    with patch("socket.getaddrinfo", _fake_resolver()), \
         patch("curl_cffi.requests.get", side_effect=[
             _cffi_resp(302, headers={"location": "http://169.254.169.254/latest/meta-data/"}),
         ]) as mock_get, \
         patch("llm_api.deepseek_api.request_with_retry") as mock_fb:
        html = fetch_html("https://example.com/open-redirect")

    assert html is None
    assert mock_get.call_count == 1  # never fetched the metadata target
    mock_fb.assert_not_called()      # blocked hop must not hit the fallback either


def test_fetch_html_blocks_scheme_relative_internal_redirect():
    with patch("socket.getaddrinfo", _fake_resolver()), \
         patch("curl_cffi.requests.get", side_effect=[
             _cffi_resp(302, headers={"location": "//192.168.0.10/internal"}),
         ]) as mock_get, \
         patch("llm_api.deepseek_api.request_with_retry") as mock_fb:
        assert fetch_html("https://example.com/a") is None
    assert mock_get.call_count == 1
    mock_fb.assert_not_called()


def test_fetch_html_caps_redirect_hops():
    endless = [_cffi_resp(302, headers={"location": f"https://example.com/h{n}"}) for n in range(10)]
    with patch("socket.getaddrinfo", _fake_resolver()), \
         patch("curl_cffi.requests.get", side_effect=endless) as mock_get, \
         patch("llm_api.deepseek_api.request_with_retry") as mock_fb:
        assert fetch_html("https://example.com/loop") is None
    assert mock_get.call_count <= 6  # 1 + max hops
    mock_fb.assert_not_called()


def test_fetch_html_rejects_oversized_body():
    huge = "x" * (MAX_RESPONSE_BYTES + 1)
    with patch("socket.getaddrinfo", _fake_resolver()), \
         patch("curl_cffi.requests.get", return_value=_cffi_resp(200, text=huge)), \
         patch("llm_api.deepseek_api.request_with_retry") as mock_fb:
        assert fetch_html("https://example.com/huge") is None
    mock_fb.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
#  fetch_html — requests fallback path
# ═════════════════════════════════════════════════════════════════════════════

def test_fetch_html_fallback_redirect_to_internal_is_blocked():
    fb1 = MagicMock()
    fb1.status_code = 302
    fb1.headers = {"location": "http://10.9.9.9/secret"}
    with patch("socket.getaddrinfo", _fake_resolver()), \
         patch("curl_cffi.requests.get", side_effect=TimeoutError("curl down")), \
         patch("llm_api.deepseek_api.request_with_retry", MagicMock(return_value=fb1)) as mock_fb:
        assert fetch_html("https://example.com/r") is None
    assert mock_fb.call_count == 1


def test_fetch_html_fallback_follows_public_redirect():
    fb1 = MagicMock()
    fb1.status_code = 302
    fb1.headers = {"location": "/final"}
    fb2 = MagicMock()
    fb2.status_code = 200
    fb2.text = "<html>fallback final</html>"
    fb2.raise_for_status = MagicMock()
    with patch("socket.getaddrinfo", _fake_resolver()), \
         patch("curl_cffi.requests.get", side_effect=TimeoutError("curl down")), \
         patch("llm_api.deepseek_api.request_with_retry", MagicMock(side_effect=[fb1, fb2])) as mock_fb:
        html = fetch_html("https://example.com/r")

    assert html == "<html>fallback final</html>"
    assert mock_fb.call_count == 2
