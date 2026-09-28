"""Isolated local settings. Never reads the existing application's .env."""

import json
import math
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
_ENV_LOCK = threading.Lock()
_SECRET_NAMES = ("OPENROUTER_API_KEY", "EXA_API_KEY")


def _load_api_keys_from_env_file() -> None:
    """Load only Chatbox API keys; never import unrelated legacy settings."""
    if not ENV_PATH.is_file():
        return
    with ENV_PATH.open(encoding="utf-8-sig") as source:
        for raw_line in source:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            if name in _SECRET_NAMES:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                if not os.environ.get(name, "").strip():
                    os.environ[name] = value


def save_api_keys(openrouter_api_key: str | None) -> dict[str, bool]:
    """Atomically update only the allowlisted secret in the ignored .env."""
    updates = {"OPENROUTER_API_KEY": openrouter_api_key}
    updates = {name: value.strip() for name, value in updates.items() if value is not None and value.strip()}
    for value in updates.values():
        if not 8 <= len(value) <= 512 or any(char.isspace() for char in value):
            raise ValueError("Invalid API key format: it should be 8–512 characters with no whitespace.")
    with _ENV_LOCK:
        lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines() if ENV_PATH.is_file() else []
        result = []
        written = set()
        for line in lines:
            stripped = line.strip()
            name = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
            if name in updates:
                if name not in written:
                    result.append(f"{name}={updates[name]}")
                    written.add(name)
                continue
            result.append(line)
        for name, value in updates.items():
            if name not in written:
                result.append(f"{name}={value}")
        temporary = ENV_PATH.with_name(".env.chatbox.tmp")
        temporary.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")
        os.replace(temporary, ENV_PATH)
        for name, value in updates.items():
            os.environ[name] = value
    return {"llm_configured": bool(os.getenv("OPENROUTER_API_KEY", "").strip())}


def configure() -> dict:
    _load_api_keys_from_env_file()
    with (ROOT / "config/chatbox.example.json").open(encoding="utf-8") as source:
        settings = json.load(source)
    local = ROOT / "config/chatbox.local.json"
    if local.is_file():
        with local.open(encoding="utf-8-sig") as source:
            overrides = json.load(source)
        if not isinstance(overrides, dict) or set(overrides) - set(settings):
            raise ValueError("chatbox.local.json contains invalid configuration keys")
        settings.update(overrides)
    for key in ("llm_model", "vision_model"):
        if not isinstance(settings[key], str):
            raise ValueError(f"{key} must be a string")
        if not settings[key].strip():
            raise ValueError(f"{key} cannot be empty")
    cap = settings["daily_spend_cap_usd"]
    if type(cap) not in (int, float) or not math.isfinite(cap) or cap <= 0:
        raise ValueError("daily_spend_cap_usd must be positive")
    if type(settings["port"]) is not int or not 1024 <= settings["port"] <= 65535:
        raise ValueError("port must be an integer between 1024 and 65535")
    if type(settings["max_upload_mb"]) is not int or not 1 <= settings["max_upload_mb"] <= 200:
        raise ValueError("max_upload_mb must be an integer between 1 and 200")
    # Set before importing ANY legacy pipeline. No production .env, persistence
    # or notification credentials are inherited by this dedicated entrypoint.
    os.environ.update({
        "MCGILL_SKIP_DOTENV": "1", "CHATBOX_OPENROUTER_ONLY": "1",
        "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY", "").strip(),
        "LLM_COMPLETIONS_URL": "https://openrouter.ai/api/v1/chat/completions",
        "LLM_DEFAULT_MODEL": settings["llm_model"],
        "OPENROUTER_VISION_MODEL": settings["vision_model"],
        "DAILY_SPEND_CAP_USD": str(cap), "DEBUG_RESPONSES": "false",
        "HF_SPEND_DATASET": "", "HF_TOKEN": "",
        "DISCORD_WEBHOOK_URL": "", "SCAFFOLD_ENABLED": "true",
    })
    return settings
