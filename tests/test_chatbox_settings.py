"""Secret-file behavior without touching the developer's real .env."""
import os

from core import chatbox_settings


def test_loads_only_allowlisted_api_keys(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=do-not-load\nOPENROUTER_API_KEY=or-test-key\n", encoding="utf-8")
    monkeypatch.setattr(chatbox_settings, "ENV_PATH", env_path)
    for name in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    chatbox_settings._load_api_keys_from_env_file()
    assert os.getenv("OPENROUTER_API_KEY") == "or-test-key"
    assert os.getenv("DEEPSEEK_API_KEY") is None


def test_save_keys_preserves_unrelated_env_lines(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("# existing\nCANLII_API_KEY=keep-this\nOPENROUTER_API_KEY=old-value\n", encoding="utf-8")
    monkeypatch.setattr(chatbox_settings, "ENV_PATH", env_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    status = chatbox_settings.save_api_keys("openrouter-new-key")
    content = env_path.read_text(encoding="utf-8")
    assert "CANLII_API_KEY=keep-this" in content
    assert content.count("OPENROUTER_API_KEY=") == 1
    assert "OPENROUTER_API_KEY=openrouter-new-key" in content
    assert status == {"llm_configured": True}
