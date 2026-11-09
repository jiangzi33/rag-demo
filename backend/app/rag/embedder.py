"""
RAG 第二步：把文本（切分块 / 用户问题）转换成向量。

使用本地 Ollama 的原生接口 POST {base_url}/api/embed（支持批量输入），先拉取模型：

    ollama pull qwen3-embedding:0.6b

- 按 batch_size 分批请求，校验返回条数
- 模型加载中 / 服务端错误（5xx）、网络错误时指数退避重试；模型不存在等 4xx 直接报错
- 默认把向量归一化为单位长度，之后检索时余弦相似度 = 点积
- 文档和问题分两个方法向量化：qwen3-embedding / bge 等模型的问题需要加检索指令前缀
"""
import math
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from functools import lru_cache

import httpx

from app.core.config import settings

_RETRY_STATUS = {429, 500, 502, 503, 504}


class EmbeddingError(RuntimeError):
    """向量化失败：配置错误、接口报错或返回格式不符。"""


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class Embedder(ABC):
    """向量化工具的统一接口，后续换实现时上层代码不用改。"""

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """把切分块的文本（Chunk.embedding_text）批量转成向量，顺序与输入一致。"""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """把用户问题转成向量。"""


class OllamaEmbedder(Embedder):
    def __init__(
        self,
        base_url: str,
        model: str,
        dimensions: int | None = None,
        batch_size: int = 16,
        query_instruction: str = "",
        normalize: bool = True,
        keep_alive: str | None = None,
        timeout: float = 60,
        max_retries: int = 3,
        client: httpx.Client | None = None,
    ):
        if not base_url or not model:
            raise EmbeddingError("未配置向量化服务：请设置 OLLAMA_BASE_URL 和 OLLAMA_EMBEDDING_MODEL")
        if batch_size <= 0:
            raise ValueError("batch_size 必须大于 0")
        self.model = model
        self.dimensions = dimensions  # 截断输出维度（qwen3-embedding 支持 32~1024），不填用模型默认维度
        self.batch_size = batch_size
        self.query_instruction = query_instruction
        self.normalize = normalize
        self.keep_alive = keep_alive  # 模型在内存中保留多久，如 "30m"，不填用 Ollama 默认值
        self.max_retries = max_retries
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    # ---------- 对外接口 ----------

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed_batch(list(texts[i: i + self.batch_size])))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([self.query_instruction + text])[0]

    def close(self) -> None:
        self._client.close()

    # ---------- 内部实现 ----------

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        # 超过模型上下文长度的输入由 Ollama 截断，而不是报错
        payload = {"model": self.model, "input": [t if t.strip() else " " for t in texts], "truncate": True}
        if self.dimensions:
            payload["dimensions"] = self.dimensions
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        vectors = self._post(payload).get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingError(f"向量化接口返回 {len(vectors or [])} 条结果，期望 {len(texts)} 条")
        return [_normalize(v) for v in vectors] if self.normalize else vectors

    def _post(self, payload: dict) -> dict:
        for attempt in range(self.max_retries + 1):
            try:
                r = self._client.post("/api/embed", json=payload)
            except httpx.TransportError as e:  # Ollama 未启动、超时等
                if attempt == self.max_retries:
                    raise EmbeddingError(f"Ollama 请求失败（是否已启动 ollama serve？）：{e}") from e
            else:
                if r.status_code == 200:
                    return r.json()
                if r.status_code not in _RETRY_STATUS or attempt == self.max_retries:
                    # 模型未拉取时返回 404：model "xxx" not found, try pulling it first
                    raise EmbeddingError(f"Ollama 返回 {r.status_code}：{r.text[:500]}")
            time.sleep(min(2 ** attempt, 10))  # 1s、2s、4s……
        raise AssertionError("unreachable")


@lru_cache
def get_embedder() -> Embedder:
    """按配置创建全局共用的向量化工具（复用 HTTP 连接池）。"""
    return OllamaEmbedder(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.OLLAMA_EMBEDDING_MODEL,
        dimensions=settings.EMBEDDING_DIMENSIONS,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
        query_instruction=settings.EMBEDDING_QUERY_INSTRUCTION,
        keep_alive=settings.OLLAMA_KEEP_ALIVE,
        timeout=settings.EMBEDDING_TIMEOUT,
    )
