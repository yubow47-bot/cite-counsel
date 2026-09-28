"""Global daily spend cap tracker — thread-safe, in-memory.

Tracks cumulative USD spend across ALL paid LLM providers for the current
UTC day. When DAILY_SPEND_CAP_USD is reached, is_over_cap() returns True
and the FastAPI endpoints return 503.

Prices: conservative over-estimate, ignore cache/tier discounts.
Pricing sources (official, dated 2026-06):
- DeepSeek: https://api-docs.deepseek.com/quick_start/pricing  (CNY)
- Gemini:   https://cloud.google.com/vertex-ai/generative-ai/pricing (USD)

DeepSeek prices are in CNY and converted to USD at spend time.
"""

import logging
import os
import threading
import time


logger = logging.getLogger(__name__)

# ── FX rate for CNY→USD conversion ──
# 1 USD ≈ 7.1 CNY as of 2026-06. Chosen low (conservative): lower rate =>
# higher USD cost => earlier cap trip => safer against overspend.
# Approximate — recalibrate from real billing if DeepSeek is the dominant cost.
USD_PER_CNY = 1.0 / 7.1

# ── Pricing table (cache-miss / standard tier — most expensive rate) ──
# DeepSeek prices in CNY per 1M tokens (converted to USD at spend time).
# Gemini prices in USD per 1M tokens (no conversion needed).
_PRICING: dict[str, dict[str, float]] = {
    "deepseek-v4-flash":      {"input": 1.0,  "output": 2.0},   # ¥1 / ¥2
    "deepseek-v4-pro":        {"input": 3.0,  "output": 6.0},   # ¥3 / ¥6
    "gemini-2.5-flash-lite":  {"input": 0.10, "output": 0.40},  # $0.10 / $0.40
    "gemini-2.5-flash":       {"input": 0.30, "output": 2.50},  # $0.30 / $2.50
    # OpenRouter (USD, 2026-09-08 pricing)
    "qwen/qwen3.7-flash":     {"input": 0.03, "output": 0.13},
    "openai/gpt-oss-20b":     {"input": 0.03, "output": 0.13},
    "z-ai/glm-4.7-flash":     {"input": 0.0605, "output": 0.40},
    "z-ai/glm-5.3-flash":     {"input": 0.15, "output": 0.50},  # OpenRouter, checked 2026-09-23
}

# Models priced in CNY (need FX conversion at spend time).
_CNY_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}

# Fallback for unknown model: highest USD/Mtokens rate across all models.
# Currently gemini-2.5-flash output: $2.50.
_FALLBACK_RATE = max(
    max(m["input"], m["output"]) * (USD_PER_CNY if model in _CNY_MODELS else 1.0)
    for model, m in _PRICING.items()
)


def normalize_model_key(model: str) -> str:
    """Return the canonical pricing key for known provider model aliases.

    This is intentionally a narrow allowlist. Unknown values remain unknown
    and retain the conservative fallback behaviour in ``_compute_cost``.
    """
    normalized = (model or "").strip().lower()
    return normalized

_DAILY_CAP = float(os.getenv("DAILY_SPEND_CAP_USD", "10"))


class SpendTracker:
    """Thread-safe in-memory global spend counter.

    Persistence was intentionally removed together with the Hugging Face
    integration: the daily cap now resets on process restart.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._utc_date: str = time.strftime("%Y-%m-%d", time.gmtime())
        self._total_spend: float = 0.0

    # ── public API ──

    def record_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> None:
        """Record tokens for a paid LLM call and update the counter."""
        cost = self._compute_cost(provider, model, input_tokens, output_tokens)
        with self._lock:
            self._check_day_rollover()
            self._total_spend += cost

    def is_over_cap(self) -> bool:
        """Return True if cumulative spend has reached the daily cap."""
        with self._lock:
            self._check_day_rollover()
            return self._total_spend >= _DAILY_CAP

    @property
    def total_spend(self) -> float:
        with self._lock:
            self._check_day_rollover()
            return self._total_spend

    @property
    def remaining_budget(self) -> float:
        return max(0.0, _DAILY_CAP - self.total_spend)

    # ── internal ──

    def _compute_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        """Compute USD cost from token counts using the pricing table.

        DeepSeek prices are stored in CNY and converted via USD_PER_CNY.
        Gemini prices are stored in USD directly.
        """
        pricing_key = normalize_model_key(model)
        rates = _PRICING.get(pricing_key)
        if rates is None:
            logger.warning("Unknown model %s — using fallback rate $%.4f/1M", model, _FALLBACK_RATE)
            input_rate = output_rate = _FALLBACK_RATE
        else:
            input_rate = rates["input"]
            output_rate = rates["output"]

        cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000

        # Convert CNY→USD for DeepSeek models
        if pricing_key in _CNY_MODELS:
            cost *= USD_PER_CNY

        return cost

    def _check_day_rollover(self):
        """Reset spend if the UTC date has changed."""
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if self._utc_date != today:
            self._utc_date = today
            self._total_spend = 0.0


# Module-level singleton — import this from anywhere
spend_tracker = SpendTracker()
