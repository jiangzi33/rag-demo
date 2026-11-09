import math
import uuid

import pytest

from app.core.config import settings
from app.core.exceptions import BizError, ErrorCode
from app.rag import Embedder, EmbeddingError
from app.rag.vector_store import MilvusVectorStore, VectorRecord
from app.services import retrieval_service


class FakeEmbedder(Embedder):
    def __init__(self, dim=3, fail=False):
        self.dim, self.fail = dim, fail
        self.queries: list[str] = []

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text):
        if self.fail:
            raise EmbeddingError("connection refused")
        self.queries.append(text)
        return [1.0] + [0.0] * (self.dim - 1)


class FakeStore:
    def __init__(self, dim):
        self.dim = dim

    def dimension(self):
        if isinstance(self.dim, Exception):
            raise self.dim
        return self.dim


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_ENABLED", True)

    def _setup(embedder=None, store_dim=3):
        embedder = embedder or FakeEmbedder()
        monkeypatch.setattr(retrieval_service, "get_embedder", lambda: embedder)
        monkeypatch.setattr(retrieval_service, "get_vector_store", lambda: FakeStore(store_dim))
        return embedder

    return _setup


def test_embed_question(setup):
    embedder = setup()
    assert retrieval_service.embed_question("  Linux\n 怎么\t安装？ ") == [1.0, 0.0, 0.0]
    assert embedder.queries == ["Linux 怎么 安装？"]  # 空白被折叠


def test_no_collection_yet_skips_dimension_check(setup):
    setup(store_dim=None)
    assert len(retrieval_service.embed_question("问题")) == 3


def test_invalid_question(setup, monkeypatch):
    setup()
    for q in ["", "   \n\t", None]:
        with pytest.raises(BizError, match="不能为空") as e:
            retrieval_service.embed_question(q)
        assert e.value.code == ErrorCode.PARAM_INVALID
    monkeypatch.setattr(settings, "RAG_QUESTION_MAX_LENGTH", 5)
    assert retrieval_service.embed_question("12345")
    with pytest.raises(BizError, match="不能超过 5"):
        retrieval_service.embed_question("123456")


def test_dimension_mismatch(setup):
    setup(FakeEmbedder(dim=768), store_dim=1024)
    with pytest.raises(BizError, match="768 维.*1024 维") as e:
        retrieval_service.embed_question("问题")
    assert (e.value.code, e.value.http_status) == (ErrorCode.RAG_UNAVAILABLE, 503)


def test_services_unavailable(setup, monkeypatch):
    setup(FakeEmbedder(fail=True))
    with pytest.raises(BizError, match="向量化服务连接失败"):
        retrieval_service.embed_question("问题")
    setup(store_dim=ConnectionError("milvus down"))
    with pytest.raises(BizError, match="向量数据库连接失败"):
        retrieval_service.embed_question("问题")
    monkeypatch.setattr(settings, "EMBEDDING_ENABLED", False)
    with pytest.raises(BizError, match="未开启向量化"):
        retrieval_service.embed_question("问题")


# ---------- 真实 Ollama + Milvus ----------

def _services_ready() -> bool:
    import socket

    import httpx

    try:
        tags = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=1).json()["models"]
        host, _, port = settings.MILVUS_URI.split("://")[-1].partition(":")
        socket.create_connection((host, int(port or 19530)), timeout=1).close()
    except (OSError, httpx.HTTPError, KeyError, ValueError):
        return False
    return any(m["name"] == settings.OLLAMA_EMBEDDING_MODEL for m in tags)


@pytest.mark.skipif(not _services_ready(), reason="本地 Ollama / Milvus 未启动")
def test_real_question_vector_matches_collection(monkeypatch):
    """真实链路：文档向量入库后，问题向量与集合维度一致，并能检索到相关块。"""
    from app.rag.embedder import OllamaEmbedder

    monkeypatch.setattr(settings, "EMBEDDING_ENABLED", True)
    embedder = OllamaEmbedder(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.OLLAMA_EMBEDDING_MODEL,
        query_instruction=settings.EMBEDDING_QUERY_INSTRUCTION,
    )
    store = MilvusVectorStore(settings.MILVUS_URI, f"test_rag_{uuid.uuid4().hex[:12]}", settings.MILVUS_TOKEN)
    monkeypatch.setattr(retrieval_service, "get_embedder", lambda: embedder)
    monkeypatch.setattr(retrieval_service, "get_vector_store", lambda: store)
    try:
        texts = ["部署手册 > Linux\n\n执行 ./install.sh 完成安装。", "报销制度 > 差旅\n\n住宿每晚不超过 500 元。"]
        vectors = embedder.embed_documents(texts)
        store.replace_document(1, [VectorRecord(i + 1, 1, 1, i, "", t, v) for i, (t, v) in enumerate(zip(texts, vectors))])

        q = retrieval_service.embed_question("Linux 怎么安装？")
        assert len(q) == store.dimension() == len(vectors[0])
        assert math.isclose(math.hypot(*q), 1, rel_tol=1e-6)
        assert store.search(q, kb_ids=[1], top_k=1)[0].id == 1
    finally:
        store._client.drop_collection(store.collection)
        embedder.close()
