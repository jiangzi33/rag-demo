import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}")
os.environ.setdefault("UPLOAD_DIR", str(_tmp / "uploads"))
os.environ.setdefault("ADMIN_USERNAMES", "admin")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import search  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.rag.vector_store import SearchHit  # noqa: E402
from app.services.retrieval_service import Retriever  # noqa: E402
from tests.test_retrieval_service import FakeEmbedder, FakeStore  # noqa: E402


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
            conn.execute(text("DELETE FROM users WHERE email LIKE '%@search.com'"))
            conn.commit()


def test_search_api(client, monkeypatch):
    r = client.post("/api/auth/register", json={"username": "searchuser", "email": "searchuser@search.com", "password": "abc12345"})
    h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    kb = client.post("/api/knowledge-bases", headers=h, json={"name": "检索测试库2"}).json()["data"]["id"]
    doc = client.post(f"/api/knowledge-bases/{kb}/documents", headers=h, json={"title": "部署手册"}).json()["data"]["id"]

    store = FakeStore(hits=[SearchHit(1, doc, kb, 0, "安装 > Linux", "执行 ./install.sh", 0.69)])
    monkeypatch.setattr(settings, "EMBEDDING_ENABLED", True)
    monkeypatch.setattr(search, "get_retriever", lambda: Retriever(FakeEmbedder(), store))

    body = {"query": "Linux 怎么安装？", "kb_ids": [kb], "top_k": 3}
    assert client.post("/api/search", json=body).status_code == 401
    r = client.post("/api/search", headers=h, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["data"] == [{
        "chunk_id": 1, "doc_id": doc, "doc_title": "部署手册", "kb_id": kb, "chunk_index": 0,
        "heading": "安装 > Linux", "content": "执行 ./install.sh", "score": 0.69,
    }]
    assert store.searches[0]["top_k"] == 3

    assert client.post("/api/search", headers=h, json={**body, "kb_ids": []}).status_code == 422
    r = client.post("/api/search", headers=h, json={**body, "query": "   "})
    assert r.status_code == 400 and r.json()["message"] == "问题不能为空"
