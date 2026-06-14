"""Lightweight DeepSeek daily call counter with configurable cap.

Persistence: data/deepseek_usage.json (one line per day).
Resets automatically each calendar day.
"""

import os
import json
import time
import threading
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
USAGE_FILE = DATA_DIR / "deepseek_usage.json"

_lock = threading.Lock()


class BudgetExceeded(Exception):
    """Raised when daily DeepSeek budget is exhausted."""


class SpendTracker:
    """Per-process daily DeepSeek call limit.

    Environment:
        DEEPSEEK_DAILY_LIMIT (int, default 500): max DeepSeek API calls/day.
    """

    def __init__(self):
        self.daily_limit = int(os.getenv("DEEPSEEK_DAILY_LIMIT", "500"))
        self._load()

    # -----------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------
    def _load(self):
        today = time.strftime("%Y-%m-%d")
        self._today = today
        self._count = 0
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            if USAGE_FILE.exists():
                raw = USAGE_FILE.read_text(encoding="utf-8")
                data = json.loads(raw)
                if data.get("date") == today:
                    self._count = data.get("count", 0)
        except Exception:
            self._count = 0

    def _save(self):
        try:
            USAGE_FILE.write_text(
                json.dumps({"date": self._today, "count": self._count}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------
    def check(self) -> tuple[bool, str | None]:
        """Return (ok, reason).  Raises BudgetExceeded if over limit."""
        with _lock:
            self._load()
            if self._count >= self.daily_limit:
                reason = f"DeepSeek API 调用已达今日上限 ({self.daily_limit} 次)"
                return False, reason
            return True, None

    def increment(self):
        with _lock:
            self._load()
            self._count += 1
            self._save()

    @property
    def count(self) -> int:
        with _lock:
            self._load()
            return self._count

    @property
    def remaining(self) -> int:
        return max(0, self.daily_limit - self.count)


# Module-level singleton
tracker = SpendTracker()
