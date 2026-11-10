import os
import tempfile
import uuid
from pathlib import Path

_tmp = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}")
os.environ.setdefault("UPLOAD_DIR", str(_tmp / "uploads"))
os.environ.setdefault("ADMIN_USERNAMES", "admin")

import math  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.exceptions import BizError, ErrorCode  # noqa: E402
from app.main import app  # noqa: E402
from app.rag import Embedder, EmbeddingError  # noqa: E402
from app.rag.vector_store import MilvusVectorStore, SearchHit, VectorRecord  # noqa: E402
from app.services.retrieval_service import Retriever  # noqa: E402


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
    """模拟 Milvus：按 kb_ids / doc_ids 过滤预置的命中结果，再取前 top_k。"""

    def __init__(self, dim=3, hits=(), fail=None):
        self.dim, self.hits, self.fail = dim, list(hits), fail
        self.searches: list[dict] = []

    def dimension(self):
        if self.fail == "dimension":
            raise ConnectionError("milvus down")
        return self.dim

    def search(self, vector, kb_ids, top_k=5, doc_ids=None):
        if self.fail == "search":
            raise ConnectionError("milvus down")
        self.searches.append({"kb_ids": kb_ids, "doc_ids": doc_ids, "top_k": top_k})
        allowed = [h for h in self.hits if h.kb_id in (kb_ids or []) or h.doc_id in (doc_ids or [])]
        return sorted(allowed, key=lambda h: -h.score)[:top_k]


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_ENABLED", True)


@pytest.fixture(scope="module")
def world():
    """owner 建知识库和 3 篇文档；reader 只被授权其中一篇；stranger 没有任何权限。"""
    from sqlalchemy import text

    from app.db.session import engine
    from app.mapper import permission_mapper, user_mapper
    from app.models.permission import PermissionType, TargetType

    with TestClient(app) as c:
        def login(name):
            r = c.post("/api/auth/register", json={"username": name, "email": f"{name}@retr.com", "password": "abc12345"})
            return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

        h = login("retrowner")
        login("retrreader")
        login("retrstranger")
        kb = c.post("/api/knowledge-bases", headers=h, json={"name": "检索测试库"}).json()["data"]["id"]
        docs = [
            c.post(f"/api/knowledge-bases/{kb}/documents", headers=h, json={"title": t}).json()["data"]["id"]
            for t in ("部署手册", "报销制度", "已删除文档")
        ]
        c.delete(f"/api/knowledge-bases/{kb}/documents/{docs[2]}", headers=h)
        with engine.connect() as conn:
            users = {n: user_mapper.select_by_username(conn, n) for n in ("retrowner", "retrreader", "retrstranger")}
            permission_mapper.insert_accepted(
                conn, PermissionType.READ, users["retrreader"].id, TargetType.DOC, docs[0], users["retrowner"].id
            )
            conn.commit()
        yield {"kb": kb, "docs": docs, "users": users}

        with engine.connect() as conn:
            conn.execute(text("DELETE FROM permissions"))
            conn.execute(text("DELETE FROM document_chunks"))
            conn.execute(text("DELETE FROM documents"))
            conn.execute(text("DELETE FROM knowledge_bases"))
            conn.execute(text("DELETE FROM users WHERE email LIKE '%@retr.com'"))
            conn.commit()


@pytest.fixture
def conn():
    from app.db.session import engine

    with engine.connect() as c:
        yield c


def hits_for(world):
    kb, (d1, d2, d3) = world["kb"], world["docs"]
    return [
        SearchHit(1, d1, kb, 0, "安装 > Linux", "执行 ./install.sh", 0.69),
        SearchHit(2, d1, kb, 1, "安装 > Windows", "双击 setup.exe", 0.57),
        SearchHit(3, d2, kb, 0, "差旅", "住宿每晚不超过 500 元", 0.15),
        SearchHit(4, d3, kb, 0, "", "已删除文档残留的向量", 0.9),
    ]


def test_owner_gets_top_k_with_titles(world, conn):
    store = FakeStore(hits=hits_for(world))
    embedder = FakeEmbedder()
    retriever = Retriever(embedder, store)
    chunks = retriever.retrieve(conn, world["users"]["retrowner"], " Linux\n怎么安装？", kb_ids=[world["kb"]], top_k=3)

    assert embedder.queries == ["Linux 怎么安装？"]
    assert store.searches == [{"kb_ids": [world["kb"]], "doc_ids": [], "top_k": 3}]
    # 已删除文档的残留向量被丢弃；按相似度排序并带上文档标题
    assert [(c.chunk_id, c.doc_title, c.heading, c.score) for c in chunks] == [
        (1, "部署手册", "安装 > Linux", 0.69),
        (2, "部署手册", "安装 > Windows", 0.57),
    ]


def test_doc_grant_only_searches_granted_docs(world, conn):
    store = FakeStore(hits=hits_for(world))
    chunks = Retriever(FakeEmbedder(), store).retrieve(conn, world["users"]["retrreader"], "报销标准", [world["kb"]])
    assert store.searches[0]["kb_ids"] == [] and store.searches[0]["doc_ids"] == [world["docs"][0]]
    assert {c.doc_id for c in chunks} == {world["docs"][0]}  # 看不到无权限的《报销制度》


def test_no_permission_returns_empty_without_embedding(world, conn):
    embedder, store = FakeEmbedder(), FakeStore(hits=hits_for(world))
    assert Retriever(embedder, store).retrieve(conn, world["users"]["retrstranger"], "问题", [world["kb"]]) == []
    assert embedder.queries == [] and store.searches == []


def test_min_score_and_default_top_k(world, conn, monkeypatch):
    monkeypatch.setattr(settings, "RAG_TOP_K", 1)
    store = FakeStore(hits=hits_for(world)[:3])  # 不含已删除文档的残留向量
    chunks = Retriever(FakeEmbedder(), store, min_score=0.6).retrieve(conn, world["users"]["retrowner"], "q", [world["kb"]])
    assert store.searches[0]["top_k"] == 1 and [c.chunk_id for c in chunks] == [1]
    store = FakeStore(hits=hits_for(world))
    chunks = Retriever(FakeEmbedder(), store, min_score=0.6).retrieve(conn, world["users"]["retrowner"], "q", [world["kb"]], 5)
    assert [c.chunk_id for c in chunks] == [1]  # 0.57 / 0.15 低于下限


def test_invalid_params(world, conn, monkeypatch):
    owner, kb = world["users"]["retrowner"], world["kb"]
    r = Retriever(FakeEmbedder(), FakeStore())
    for q in ["", "  \n\t", None]:
        with pytest.raises(BizError, match="不能为空"):
            r.retrieve(conn, owner, q, [kb])
    monkeypatch.setattr(settings, "RAG_QUESTION_MAX_LENGTH", 5)
    with pytest.raises(BizError, match="不能超过 5"):
        r.retrieve(conn, owner, "123456", [kb])
    with pytest.raises(BizError, match="top_k"):
        r.retrieve(conn, owner, "q", [kb], top_k=settings.RAG_TOP_K_MAX + 1)
    with pytest.raises(BizError, match="请选择"):
        r.retrieve(conn, owner, "q", [])
    with pytest.raises(BizError) as e:
        r.retrieve(conn, owner, "q", [999999])
    assert e.value.code == ErrorCode.NOT_FOUND


def test_services_unavailable(world, conn, monkeypatch):
    owner, kb = world["users"]["retrowner"], world["kb"]
    cases = [
        (FakeEmbedder(fail=True), FakeStore(), "向量化服务连接失败"),
        (FakeEmbedder(), FakeStore(fail="dimension"), "向量数据库连接失败"),
        (FakeEmbedder(), FakeStore(fail="search"), "向量数据库检索失败"),
        (FakeEmbedder(dim=768), FakeStore(dim=1024), "768 维.*1024 维"),
    ]
    for embedder, store, msg in cases:
        with pytest.raises(BizError, match=msg) as e:
            Retriever(embedder, store).retrieve(conn, owner, "q", [kb])
        assert (e.value.code, e.value.http_status) == (ErrorCode.RAG_UNAVAILABLE, 503)
    monkeypatch.setattr(settings, "EMBEDDING_ENABLED", False)
    with pytest.raises(BizError, match="未开启向量化"):
        Retriever(FakeEmbedder(), FakeStore()).retrieve(conn, owner, "q", [kb])


def test_embed_question_without_collection():
    assert Retriever(FakeEmbedder(), FakeStore(dim=None)).embed_question("问题") == [1.0, 0.0, 0.0]


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
def test_real_retrieval(world, conn):
    """真实链路：问题向量与集合维度一致，并按权限检索到最相关的片段。"""
    from app.rag.embedder import OllamaEmbedder

    embedder = OllamaEmbedder(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.OLLAMA_EMBEDDING_MODEL,
        query_instruction=settings.EMBEDDING_QUERY_INSTRUCTION,
    )
    store = MilvusVectorStore(settings.MILVUS_URI, f"test_rag_{uuid.uuid4().hex[:12]}", settings.MILVUS_TOKEN)
    kb, (d1, d2, _) = world["kb"], world["docs"]
    try:
        rows = [(d1, "部署手册 > 安装 > Linux\n\n执行 ./install.sh 完成安装。"), (d2, "报销制度 > 差旅\n\n住宿每晚不超过 500 元。")]
        vectors = embedder.embed_documents([t for _, t in rows])
        store.replace_document(d1, [VectorRecord(1, d1, kb, 0, "安装 > Linux", rows[0][1], vectors[0])])
        store.replace_document(d2, [VectorRecord(2, d2, kb, 0, "差旅", rows[1][1], vectors[1])])
        retriever = Retriever(embedder, store)

        q = retriever.embed_question("Linux 怎么安装？")
        assert len(q) == store.dimension() == len(vectors[0]) and math.isclose(math.hypot(*q), 1, rel_tol=1e-6)

        owner = retriever.retrieve(conn, world["users"]["retrowner"], "Linux 怎么安装？", [kb], top_k=2)
        assert [c.chunk_id for c in owner] == [1, 2] and owner[0].score > owner[1].score
        reader = retriever.retrieve(conn, world["users"]["retrreader"], "出差住宿标准是多少？", [kb], top_k=2)
        assert [c.doc_title for c in reader] == ["部署手册"]  # 只授权了《部署手册》
    finally:
        store._client.drop_collection(store.collection)
        embedder.close()
