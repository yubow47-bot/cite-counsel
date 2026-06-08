import requests
from config.settings import OLLAMA_HOST, OLLAMA_MODEL


def ask_ollama(prompt: str) -> str:
    """单次调用，用于 Step1 提取字段和 Step2 格式化引用，无上下文。"""
    response = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False
        }
    )
    return response.json()["response"]


def chat_ollama(messages: list) -> str:
    """多轮对话,用于选项4自由咨询,保留上下文。"""
    response = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False
        }
    )
    return response.json()["message"]["content"]