import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}")
os.environ.setdefault("UPLOAD_DIR", str(_tmp / "uploads"))
os.environ.setdefault("ADMIN_USERNAMES", "admin")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.rag import Embedder, EmbeddingError  # noqa: E402
from app.services import embedding_service  # noqa: E402


class FakeEmbedder(Embedder):
    def __init__(self, fail=False):
        self.fail = fail
        self.calls: list[list[str]] = []

    def embed_documents(self, texts):
        if self.fail:
            raise EmbeddingError("服务不可用")
        self.calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]

    def embed_query(self, text):
        return [float(len(text)), 1.0]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
        from sqlalchemy import text

        from app.db.session import engine

        with engine.connect() as conn:
            conn.execute(text("DELETE FROM document_chunks"))
            conn.execute(text("DELETE FROM documents"))
            conn.execute(text("DELETE FROM knowledge_bases"))
            conn.execute(text("DELETE FROM users WHERE email LIKE '%@embed.com'"))
            conn.commit()


@pytest.fixture(scope="module")
def base(client):
    r = client.post("/api/auth/register", json={"username": "embedder", "email": "embedder@embed.com", "password": "abc12345"})
    h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    kb_id = client.post("/api/knowledge-bases", headers=h, json={"name": "向量测试库"}).json()["data"]["id"]
    return h, f"/api/knowledge-bases/{kb_id}/documents"


def test_create_document_embeds_chunks(client, base, monkeypatch):
    fake, saved = FakeEmbedder(), []
    monkeypatch.setattr(settings, "EMBEDDING_ENABLED", True)
    monkeypatch.setattr(embedding_service, "get_embedder", lambda: fake)
    monkeypatch.setattr(embedding_service, "save_vectors", lambda doc, items: saved.append((doc, items)))

    h, url = base
    html = "<h1>安装</h1><p>下载安装包。</p><h2>Linux</h2><p>执行脚本。</p>"
    doc = client.post(url, headers=h, json={"title": "手册", "content": html}).json()["data"]

    # TestClient 在返回响应后同步执行后台任务
    texts = ["手册 > 安装\n\n下载安装包。", "手册 > 安装 > Linux\n\n执行脚本。"]
    assert fake.calls == [texts]
    [(saved_doc, items)] = saved
    assert saved_doc.id == doc["id"]
    assert [(i.chunk.chunk_index, i.text, i.vector) for i in items] == [
        (i, t, [float(len(t)), 1.0]) for i, t in enumerate(texts)
    ]

    # 空文档没有切分块，不调用向量化服务
    client.post(url, headers=h, json={"title": "空文档"})
    assert len(fake.calls) == 1 and len(saved) == 1


def test_embedding_failure_does_not_break_create(client, base, monkeypatch):
    saved = []
    monkeypatch.setattr(settings, "EMBEDDING_ENABLED", True)
    monkeypatch.setattr(embedding_service, "get_embedder", lambda: FakeEmbedder(fail=True))
    monkeypatch.setattr(embedding_service, "save_vectors", lambda doc, items: saved.append(items))

    h, url = base
    r = client.post(url, headers=h, json={"title": "手册", "content": "<p>正文</p>"})
    assert r.status_code == 200 and saved == []
    assert client.get(f"{url}/{r.json()['data']['id']}", headers=h).status_code == 200


def test_disabled_skips_embedding(monkeypatch):
    monkeypatch.setattr(embedding_service, "get_embedder", lambda: pytest.fail("不应调用向量化服务"))
    assert embedding_service.embed_document(1) == []
