"""
RAG 第二步：把文本（切分块 / 用户问题）转换成向量。

对接 OpenAI 兼容的 POST {base_url}/embeddings 接口，绝大多数服务都支持，只需改 .env 即可切换：

    # 阿里云百炼（单次最多 10 条）
    EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    EMBEDDING_MODEL=text-embedding-v3
    # 硅基流动
    EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
    EMBEDDING_MODEL=BAAI/bge-m3
    # 本地 Ollama（无需 API Key）
    EMBEDDING_BASE_URL=http://localhost:11434/v1
    EMBEDDING_MODEL=bge-m3

- 按 batch_size 分批请求，按返回的 index 还原顺序
- 限流（429）、服务端错误（5xx）、网络错误时指数退避重试
- 默认把向量归一化为单位长度，之后检索时余弦相似度 = 点积
- 文档和问题分两个方法向量化：部分模型（如 bge 系列）的问题需要加检索指令前缀
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
    """向量化工具的统一接口，后续换成本地模型等实现时上层代码不用改。"""

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """把切分块的文本（Chunk.embedding_text）批量转成向量，顺序与输入一致。"""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """把用户问题转成向量。"""


class OpenAICompatibleEmbedder(Embedder):
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        dimensions: int | None = None,
        batch_size: int = 10,
        query_instruction: str = "",
        normalize: bool = True,
        timeout: float = 30,
        max_retries: int = 3,
        client: httpx.Client | None = None,
    ):
        if not base_url or not model:
            raise EmbeddingError("未配置向量化服务：请设置 EMBEDDING_BASE_URL 和 EMBEDDING_MODEL")
        if batch_size <= 0:
            raise ValueError("batch_size 必须大于 0")
        self.model = model
        self.dimensions = dimensions  # 部分模型支持指定输出维度（如 text-embedding-v3 / text-embedding-3-*）
        self.batch_size = batch_size
        self.query_instruction = query_instruction
        self.normalize = normalize
        self.max_retries = max_retries
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

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
        # 接口不接受空字符串，用空格代替（切分块不会为空，这里只是兜底）
        payload = {"model": self.model, "input": [t if t.strip() else " " for t in texts], "encoding_format": "float"}
        if self.dimensions:
            payload["dimensions"] = self.dimensions
        data = self._post(payload).get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingError(f"向量化接口返回 {len(data or [])} 条结果，期望 {len(texts)} 条")
        vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
        return [_normalize(v) for v in vectors] if self.normalize else vectors

    def _post(self, payload: dict) -> dict:
        for attempt in range(self.max_retries + 1):
            try:
                r = self._client.post("/embeddings", json=payload)
            except httpx.TransportError as e:  # 连接失败、超时等
                if attempt == self.max_retries:
                    raise EmbeddingError(f"向量化服务请求失败：{e}") from e
            else:
                if r.status_code == 200:
                    return r.json()
                if r.status_code not in _RETRY_STATUS or attempt == self.max_retries:
                    raise EmbeddingError(f"向量化服务返回 {r.status_code}：{r.text[:500]}")
            time.sleep(min(2 ** attempt, 10))  # 1s、2s、4s……
        raise AssertionError("unreachable")


@lru_cache
def get_embedder() -> Embedder:
    """按配置创建全局共用的向量化工具（复用 HTTP 连接池）。"""
    return OpenAICompatibleEmbedder(
        base_url=settings.EMBEDDING_BASE_URL,
        model=settings.EMBEDDING_MODEL,
        api_key=settings.EMBEDDING_API_KEY,
        dimensions=settings.EMBEDDING_DIMENSIONS,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
        query_instruction=settings.EMBEDDING_QUERY_INSTRUCTION,
        timeout=settings.EMBEDDING_TIMEOUT,
    )
