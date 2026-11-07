import json
import math

import httpx
import pytest

from app.rag import EmbeddingError, OpenAICompatibleEmbedder


def make_embedder(handler, **kwargs):
    client = httpx.Client(base_url="http://test/v1", transport=httpx.MockTransport(handler))
    return OpenAICompatibleEmbedder(base_url="http://test/v1", model="m", client=client, **kwargs)


def fake_api(requests):
    """按文本长度生成二维向量 [len, 1]，并且倒序返回，检查是否按 index 还原顺序。"""
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        requests.append(body)
        data = [{"index": i, "embedding": [float(len(t)), 1.0]} for i, t in enumerate(body["input"])]
        return httpx.Response(200, json={"data": data[::-1]})
    return handler


def test_batch_order_and_normalize():
    requests = []
    embedder = make_embedder(fake_api(requests), batch_size=2, dimensions=512)
    vectors = embedder.embed_documents(["a", "bbb", "cc", "dddd", "e"])
    assert [len(r["input"]) for r in requests] == [2, 2, 1]
    assert requests[0]["model"] == "m" and requests[0]["dimensions"] == 512
    # 归一化后为单位向量，且顺序与输入一致
    assert all(math.isclose(math.hypot(*v), 1) for v in vectors)
    assert [round(v[0] / v[1]) for v in vectors] == [1, 3, 2, 4, 1]
    assert embedder.embed_documents([]) == []


def test_query_instruction_and_raw_vectors():
    requests = []
    embedder = make_embedder(fake_api(requests), query_instruction="检索：", normalize=False)
    assert embedder.embed_query("问题") == [5.0, 1.0]
    assert requests[0]["input"] == ["检索：问题"]


def test_retry_then_success(monkeypatch):
    monkeypatch.setattr("app.rag.embedder.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text="rate limited")
        if len(calls) == 2:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [3.0, 4.0]}]})

    assert make_embedder(handler).embed_query("x") == [0.6, 0.8]
    assert len(calls) == 3


def test_errors(monkeypatch):
    monkeypatch.setattr("app.rag.embedder.time.sleep", lambda _: None)
    # 4xx（如 Key 错误）不重试，直接报错
    calls = []

    def unauthorized(request):
        calls.append(1)
        return httpx.Response(401, text="invalid api key")

    with pytest.raises(EmbeddingError, match="401"):
        make_embedder(unauthorized).embed_query("x")
    assert len(calls) == 1
    # 重试次数用完
    with pytest.raises(EmbeddingError, match="503"):
        make_embedder(lambda r: httpx.Response(503), max_retries=2).embed_query("x")
    # 返回条数不符
    with pytest.raises(EmbeddingError, match="期望 2 条"):
        make_embedder(lambda r: httpx.Response(200, json={"data": []})).embed_documents(["a", "b"])
    # 未配置
    with pytest.raises(EmbeddingError):
        OpenAICompatibleEmbedder(base_url="", model="")
