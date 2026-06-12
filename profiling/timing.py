"""Thread-safe lightweight profiler for the citation pipeline.

Usage — context manager:
    with timing.measure("llm.classify", model="deepseek-chat"):
        result = ask_deepseek(prompt)

Usage — decorator:
    @timing.profile("a2aj.fetch", endpoint="/fetch")
    def fetch_by_citation(...): ...

One-click disable: set ENABLED = False.
"""

import time
import threading
from contextlib import contextmanager

ENABLED = True

_records = []
_lock = threading.Lock()


def reset():
    with _lock:
        _records.clear()


def _record(label, start, end, meta):
    with _lock:
        _records.append({
            "label": label,
            "start_s": start,
            "end_s": end,
            "duration_s": end - start,
            "meta": dict(meta) if meta else {},
        })


def dump():
    with _lock:
        return list(_records)


@contextmanager
def measure(label, **meta):
    if not ENABLED:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        end = time.perf_counter()
        _record(label, start, end, meta)


def profile(label=None, **default_meta):
    """Decorator: wrap the entire function as one timing span."""
    def decorator(func):
        name = label or func.__name__
        def wrapper(*args, **kwargs):
            if not ENABLED:
                return func(*args, **kwargs)
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                end = time.perf_counter()
                _record(name, start, end, default_meta)
        return wrapper
    return decorator
