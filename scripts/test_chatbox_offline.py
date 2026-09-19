"""Run regressions without dotenv or external socket connections."""
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update({"MCGILL_SKIP_DOTENV": "1", "HF_TOKEN": "", "HF_SPEND_DATASET": "",
                   "DISCORD_WEBHOOK_URL": "", "OPENROUTER_API_KEY": "offline-test",
                   "DEEPSEEK_API_KEY": "offline-test", "GEMINI_API_KEY": "offline-test"})

_connect = socket.socket.connect


def offline_connect(sock, address):
    # asyncio on Windows uses loopback sockets internally for its event loop.
    if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}:
        return _connect(sock, address)
    raise OSError("External sockets blocked during offline tests")


if __name__ == "__main__":
    socket.socket.connect = offline_connect
    import pytest
    raise SystemExit(pytest.main(sys.argv[1:] or [str(ROOT / "tests"), "-q"]))
