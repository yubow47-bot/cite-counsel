"""SSRF guard for user-supplied URLs.

``/api/extract/url`` fetches arbitrary user-supplied URLs from the server, so
every fetch is validated here before the request goes out: only http/https
schemes, no embedded credentials, and the host must resolve to a public
address (loopback, private ranges, link-local / cloud-metadata, reserved and
multicast addresses are all rejected).

Redirects are followed manually by the caller (``fetch_html``) so that every
hop is re-validated before the next request is issued.

DNS-rebinding note: the hostname is resolved here for validation and again by
the HTTP client at connect time.  Fully closing that gap would require pinning
the validated IP in the request itself; the small validation-to-fetch window
is accepted for this threat model (public citation tool, no internal network
authority).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

# Cap on fetched page bodies — trafilatura only needs the readable text of a
# normal article; anything near this size is not a citation source.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024

_ALLOWED_SCHEMES = {"http", "https"}


class UrlBlocked(ValueError):
    """A URL failed SSRF validation (bad scheme, bad host, or internal address)."""


def is_blocked_ip(ip_str: str) -> bool:
    """True if the address is loopback/private/link-local/reserved/etc.

    Unparseable addresses fail closed.  IPv4-mapped and 6to4 IPv6 addresses
    are unwrapped and judged by their embedded IPv4 address.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if addr.version == 6:
        embedded = addr.ipv4_mapped or addr.sixtofour
        if embedded is not None:
            addr = embedded
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def resolve_hostname(hostname: str) -> list[str]:
    """Resolve a hostname to a deduped list of IP strings.

    Raises UrlBlocked on resolution failure (fail closed — the HTTP client
    would not be able to connect either, but blocking here keeps the
    validation and the fetch on the same resolver decision).
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as e:
        raise UrlBlocked(f"cannot resolve host {hostname!r}") from e
    ips: list[str] = []
    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip = sockaddr[0]
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise UrlBlocked(f"no addresses resolved for host {hostname!r}")
    return ips


def validate_url(url: str) -> str:
    """Validate a user-supplied URL for safe outbound fetching.

    Rules: http/https only; no embedded credentials; hostname must resolve
    (via getaddrinfo) to at least one address and every resolved address must
    be public.  Returns the URL unchanged, raises UrlBlocked otherwise.
    """
    if not url or not isinstance(url, str):
        raise UrlBlocked("empty or non-string URL")
    url = url.strip()
    if len(url) > 2048:
        raise UrlBlocked("URL too long")
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise UrlBlocked(f"unparseable URL: {e}") from e
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UrlBlocked(f"scheme {parsed.scheme!r} not allowed")
    if not parsed.hostname:
        raise UrlBlocked("no hostname")
    if parsed.username is not None or parsed.password is not None:
        raise UrlBlocked("credentials in URL not allowed")
    try:
        port = parsed.port
    except ValueError as e:
        raise UrlBlocked(f"invalid port: {e}") from e
    if port is not None and not (0 < port <= 65535):
        raise UrlBlocked(f"invalid port {port}")
    for ip in resolve_hostname(parsed.hostname):
        if is_blocked_ip(ip):
            raise UrlBlocked(
                f"host {parsed.hostname!r} resolves to a blocked address ({ip})"
            )
    return url


def next_redirect_url(current_url: str, location: str) -> str:
    """Resolve a Location header against the current URL and validate the result.

    Raises UrlBlocked when the hop target is missing or fails validation.
    """
    if not location:
        raise UrlBlocked("redirect without Location header")
    return validate_url(urljoin(current_url, location))
