import json
import math

import httpx
import pytest

from app.core.config import settings
from app.rag import EmbeddingError, OllamaEmbedder


def make_embedder(handler, **kwargs):
    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
    return OllamaEmbedder(base_url="http://test", model="m", client=client, **kwargs)


def fake_api(requests):
    """模拟 Ollama /api/embed：按文本长度生成二维向量 [len, 1]。"""
    def handler(request: httpx.Request):
        assert request.url.path == "/api/embed"
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"model": body["model"], "embeddings": [[float(len(t)), 1.0] for t in body["input"]]})
    return handler


def test_batch_order_and_normalize():
    requests = []
    embedder = make_embedder(fake_api(requests), batch_size=2, dimensions=512, keep_alive="30m")
    vectors = embedder.embed_documents(["a", "bbb", "cc", "dddd", "e"])
    assert [len(r["input"]) for r in requests] == [2, 2, 1]
    assert requests[0] == {"model": "m", "input": ["a", "bbb"], "truncate": True, "dimensions": 512, "keep_alive": "30m"}
    # 归一化后为单位向量，且顺序与输入一致
    assert all(math.isclose(math.hypot(*v), 1) for v in vectors)
    assert [round(v[0] / v[1]) for v in vectors] == [1, 3, 2, 4, 1]
    assert embedder.embed_documents([]) == []


def test_query_instruction_and_raw_vectors():
    requests = []
    embedder = make_embedder(fake_api(requests), query_instruction="检索：", normalize=False)
    assert embedder.embed_query("问题") == [5.0, 1.0]
    assert requests[0]["input"] == ["检索：问题"]
    assert "dimensions" not in requests[0] and "keep_alive" not in requests[0]


def test_retry_then_success(monkeypatch):
    monkeypatch.setattr("app.rag.embedder.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, text="model is loading")
        if len(calls) == 2:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0]]})

    assert make_embedder(handler).embed_query("x") == [0.6, 0.8]
    assert len(calls) == 3


def test_errors(monkeypatch):
    monkeypatch.setattr("app.rag.embedder.time.sleep", lambda _: None)
    # 模型未拉取（404）不重试，直接报错
    calls = []

    def not_found(request):
        calls.append(1)
        return httpx.Response(404, json={"error": 'model "m" not found, try pulling it first'})

    with pytest.raises(EmbeddingError, match="404.*pulling"):
        make_embedder(not_found).embed_query("x")
    assert len(calls) == 1
    # 重试次数用完
    with pytest.raises(EmbeddingError, match="500"):
        make_embedder(lambda r: httpx.Response(500), max_retries=2).embed_query("x")
    with pytest.raises(EmbeddingError, match="ollama serve"):
        make_embedder(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")), max_retries=0).embed_query("x")
    # 返回条数不符
    with pytest.raises(EmbeddingError, match="期望 2 条"):
        make_embedder(lambda r: httpx.Response(200, json={"embeddings": []})).embed_documents(["a", "b"])
    # 未配置
    with pytest.raises(EmbeddingError):
        OllamaEmbedder(base_url="", model="")


def _ollama_ready() -> bool:
    try:
        tags = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=1).json()["models"]
    except (httpx.HTTPError, KeyError, ValueError):
        return False
    return any(m["name"] == settings.OLLAMA_EMBEDDING_MODEL for m in tags)


@pytest.mark.skipif(not _ollama_ready(), reason="本地 Ollama 未启动或未拉取向量模型")
def test_real_ollama_retrieval():
    """真实模型：问题与相关段落的相似度应高于无关段落。"""
    embedder = OllamaEmbedder(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.OLLAMA_EMBEDDING_MODEL,
        query_instruction=settings.EMBEDDING_QUERY_INSTRUCTION,
    )
    docs = embedder.embed_documents([
        "部署手册 > 安装 > Linux\n\n在 Linux 上执行 ./install.sh 完成安装，需要 root 权限。",
        "报销制度 > 差旅\n\n出差住宿标准为每晚不超过 500 元，需提供发票。",
    ])
    q = embedder.embed_query("怎么在 Linux 服务器上安装？")
    related, unrelated = (sum(a * b for a, b in zip(q, d)) for d in docs)
    assert len(q) == len(docs[0]) and math.isclose(math.hypot(*q), 1, rel_tol=1e-6)
    assert related > unrelated + 0.1
    embedder.close()
