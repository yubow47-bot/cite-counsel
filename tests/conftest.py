"""Hermetic test environment.

deepseek_api runs _load_env() at import (os.environ.setdefault), so a local
.env with LLM_COMPLETIONS_URL/LLM_DEFAULT_MODEL would flip the WHOLE test
suite onto OpenRouter/qwen.  Pin the test process to the DeepSeek-direct
defaults BEFORE any test module imports the pipeline; conftest.py is imported
by pytest before test module collection, which guarantees the ordering.
"""

import os

os.environ["LLM_COMPLETIONS_URL"] = "https://api.deepseek.com/chat/completions"
os.environ["LLM_DEFAULT_MODEL"] = "deepseek-v4-flash"
