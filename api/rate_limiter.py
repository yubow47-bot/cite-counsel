"""Simple in-memory IP-based rate limiter (no external dependency).

Env: RATE_LIMIT_PER_MINUTE (int, default 30) — max requests/IP/minute.
"""

import os
import time
from collections import defaultdict

RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))


class RateLimiter:
    """Token-bucket-style limiter: each IP gets N requests per sliding 60 s window."""

    def __init__(self):
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, ip: str) -> bool:
        now = time.time()
        bucket = self._buckets[ip]
        # prune entries older than 60 s
        cutoff = now - 60
        self._buckets[ip] = [t for t in bucket if t > cutoff]
        if len(self._buckets[ip]) >= RATE_LIMIT:
            return False
        self._buckets[ip].append(now)
        return True
