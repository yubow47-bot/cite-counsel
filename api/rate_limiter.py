"""Simple in-memory IP-based rate limiter (no external dependency).

Env:
  RATE_LIMIT_PER_MIN  (int, default 30)  — max requests/IP/minute.
  RATE_LIMIT_PER_HOUR (int, default 200) — max requests/IP/hour.

IP source: leftmost entry of X-Forwarded-For (real client behind HF proxy).
Rate limiting is best-effort (XFF is client-spoofable); the global spend cap
is the real backstop.

Memory: the IP table is bounded.  Spoofed XFF values would otherwise create a
permanent dict entry per fake IP (the limiter itself never blocks a first-time
IP), so the table is swept whenever it grows past _MAX_TRACKED_IPS.
"""

import os
import time
from collections import defaultdict

# Env ints are attacker-influenceable only via deployment config, but a bad
# value must degrade to the default rather than crash or disable the limiter.
def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


RATE_PER_MIN = _int_env("RATE_LIMIT_PER_MIN", 30)
RATE_PER_HOUR = _int_env("RATE_LIMIT_PER_HOUR", 200)

# Window lengths in seconds
_WINDOW_MIN = 60
_WINDOW_HOUR = 3600

# Hard cap on tracked IPs — sweep trigger.  Each tracked IP holds at most
# RATE_PER_HOUR timestamps (in-window pruning), so worst-case memory is
# _MAX_TRACKED_IPS * RATE_PER_HOUR floats.
_MAX_TRACKED_IPS = 10_000


def extract_client_ip(request) -> str:
    """Extract real client IP from X-Forwarded-For (HF Spaces reverse proxy).

    Leftmost entry is the original client. Falls back to request.client.host
    when no XFF header is present (local dev).
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Per-IP sliding-window rate limiter (minute + hour) with a bounded IP table."""

    def __init__(self):
        # {ip: [timestamp, ...]}
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, ip: str) -> bool:
        """Check and record a request for *ip*. Returns True if allowed."""
        now = time.time()
        cutoff_hour = now - _WINDOW_HOUR
        cutoff_min = now - _WINDOW_MIN

        bucket = self._buckets[ip]

        # Prune entries older than 1 hour (one pass covers both windows)
        bucket[:] = [t for t in bucket if t > cutoff_hour]

        # Count in each window
        count_min = sum(1 for t in bucket if t > cutoff_min)
        count_hour = len(bucket)

        if count_min >= RATE_PER_MIN or count_hour >= RATE_PER_HOUR:
            return False

        bucket.append(now)

        if len(self._buckets) > _MAX_TRACKED_IPS:
            self._sweep(cutoff_hour)
        return True

    def _sweep(self, cutoff_hour: float) -> None:
        """Drop expired and empty buckets to bound the IP table.

        Called only when the table exceeds _MAX_TRACKED_IPS (spoofed-XFF
        flood), so the O(n) walk is amortized over many requests.
        """
        empty: list[str] = []
        for key, bucket in self._buckets.items():
            bucket[:] = [t for t in bucket if t > cutoff_hour]
            if not bucket:
                empty.append(key)
        for key in empty:
            del self._buckets[key]
