"""Simple in-memory IP-based rate limiter (no external dependency).

Env:
  RATE_LIMIT_PER_MIN  (int, default 30)  — max requests/IP/minute.
  RATE_LIMIT_PER_HOUR (int, default 200) — max requests/IP/hour.

IP source: leftmost entry of X-Forwarded-For (real client behind HF proxy).
Rate limiting is best-effort (XFF is client-spoofable); the global spend cap
is the real backstop.
"""

import os
import time
from collections import defaultdict


RATE_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))
RATE_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", "200"))

# Window lengths in seconds
_WINDOW_MIN = 60
_WINDOW_HOUR = 3600


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
    """Per-IP sliding-window rate limiter (minute + hour)."""

    def __init__(self):
        # {ip: [timestamp, ...]}
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, ip: str) -> bool:
        """Check and record a request for *ip*. Returns True if allowed."""
        now = time.time()
        cutoff_min = now - _WINDOW_MIN
        cutoff_hour = now - _WINDOW_HOUR

        bucket = self._buckets[ip]

        # Prune entries older than 1 hour (one pass covers both windows)
        bucket[:] = [t for t in bucket if t > cutoff_hour]

        # Count in each window
        count_min = sum(1 for t in bucket if t > cutoff_min)
        count_hour = len(bucket)

        if count_min >= RATE_PER_MIN or count_hour >= RATE_PER_HOUR:
            return False

        bucket.append(now)
        return True
