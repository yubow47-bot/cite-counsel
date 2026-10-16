"""List-compatible source results with explicit search coverage and failures."""

from __future__ import annotations


class SourceResults(list):
    """Keep legacy iteration/list comparisons while retaining connector status."""

    def __init__(self, values=(), *, coverage: dict | None = None):
        super().__init__(values)
        self.coverage = coverage or {"status": "complete"}


class SourceRequestError(RuntimeError):
    """An unavailable source is different from a successful empty search."""

    def __init__(self, source: str, operation: str, *, reason: str = "request_failed",
                 coverage: dict | None = None, retryable: bool = True):
        self.source, self.operation, self.reason = source, operation, reason
        self.coverage = coverage or {"status": "failed", "source": source, "operation": operation}
        self.retryable = retryable
        super().__init__(f"{source} {operation} failed ({reason}); search coverage is incomplete.")

    def content(self) -> dict:
        return {"error": str(self), "kind": "source_unavailable", "retryable": self.retryable,
                "source": self.source, "operation": self.operation, "reason": self.reason,
                "coverage": self.coverage}


def failure_reason(exc: Exception) -> str:
    """Useful error classes without echoing credentials, URLs or response bodies."""
    import requests
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.exceptions.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return f"http_{status}" if status else "http_error"
    if isinstance(exc, (ValueError, TypeError)):
        return "invalid_response"
    return "request_failed"
