"""
RAG 生成：调用 OpenAI Chat Completions 接口（流式），根据检索到的片段生成回答。

直接请求 POST {base_url}/chat/completions，不依赖 openai SDK；兼容 OpenAI 协议的服务改 .env 即可切换：

    OPENAI_API_KEY=sk-...
    OPENAI_MODEL=gpt-4.1-mini
    # 其他兼容服务，如 DeepSeek / 阿里云百炼 / 本地 Ollama（http://localhost:11434/v1，API Key 任填）
    OPENAI_BASE_URL=https://api.deepseek.com/v1
"""
import json
from collections.abc import Iterator
from functools import lru_cache

import httpx

from app.core.config import settings


class ChatError(RuntimeError):
    """大模型调用失败：未配置、接口报错或网络错误。"""


class OpenAIChatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float | None = None,
        timeout: float = 120,
        client: httpx.Client | None = None,
    ):
        if not api_key:
            raise ChatError("未配置 OPENAI_API_KEY")
        self.model = model
        self.temperature = temperature
        # 流式响应：连接 / 首字节超时用 timeout，读取每个数据块之间也以 timeout 为限
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )

    def stream(self, messages: list[dict]) -> Iterator[str]:
        """流式生成，逐段返回新增的文字。"""
        payload = {"model": self.model, "messages": messages, "stream": True}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        try:
            with self._client.stream("POST", "/chat/completions", json=payload) as r:
                if r.status_code != 200:
                    r.read()
                    raise ChatError(f"大模型接口返回 {r.status_code}：{_error_message(r)}")
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    chunk = json.loads(data)
                    if chunk.get("error"):
                        raise ChatError(f"大模型生成出错：{chunk['error'].get('message', chunk['error'])}")
                    for choice in chunk.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield delta
        except httpx.HTTPError as e:  # 连接失败、超时等
            raise ChatError(f"大模型接口请求失败：{e}") from e

    def close(self) -> None:
        self._client.close()


def _error_message(r: httpx.Response) -> str:
    try:
        return r.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return r.text[:300]


@lru_cache
def get_chat_client() -> OpenAIChatClient:
    """按配置创建全局共用的客户端；未配置 API Key 时抛出 ChatError（不缓存，配置后重启即可）。"""
    return OpenAIChatClient(
        base_url=settings.OPENAI_BASE_URL,
        api_key=settings.OPENAI_API_KEY,
        model=settings.OPENAI_MODEL,
        temperature=settings.OPENAI_TEMPERATURE,
        timeout=settings.OPENAI_TIMEOUT,
    )
