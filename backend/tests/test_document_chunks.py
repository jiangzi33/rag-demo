import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp / 'test.db'}")
os.environ.setdefault("UPLOAD_DIR", str(_tmp / "uploads"))
os.environ.setdefault("ADMIN_USERNAMES", "admin")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


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
            conn.execute(text("DELETE FROM users WHERE email LIKE '%@chunk.com'"))
            conn.commit()


def chunks_of(doc_id):
    from app.db.session import engine
    from app.mapper import document_chunk_mapper

    with engine.connect() as conn:
        return document_chunk_mapper.select_by_doc_id(conn, doc_id)


def test_chunks_follow_document_lifecycle(client):
    r = client.post("/api/auth/register", json={"username": "chunker", "email": "chunker@chunk.com", "password": "abc12345"})
    h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    kb_id = client.post("/api/knowledge-bases", headers=h, json={"name": "切分测试库"}).json()["data"]["id"]
    base = f"/api/knowledge-bases/{kb_id}/documents"

    # 新建文档时自动切分
    html = "<h1>安装</h1><p>下载安装包。</p><h2>Linux</h2><p>执行脚本。</p>"
    doc = client.post(base, headers=h, json={"title": "手册", "content": html}).json()["data"]
    chunks = chunks_of(doc["id"])
    assert [(c.chunk_index, c.heading_path, c.content) for c in chunks] == [
        (0, ["安装"], "下载安装包。"),
        (1, ["安装", "Linux"], "执行脚本。"),
    ]
    assert all(c.kb_id == kb_id for c in chunks)

    # 空正文不产生切分块
    empty = client.post(base, headers=h, json={"title": "空文档"}).json()["data"]
    assert chunks_of(empty["id"]) == []

    # 编辑后整篇重新切分，旧块不残留
    client.put(f"{base}/{doc['id']}", headers=h, json={"title": "手册", "content": "<p>新正文</p>"})
    assert [(c.chunk_index, c.heading_path, c.content) for c in chunks_of(doc["id"])] == [(0, [], "新正文")]

    # 删除文档 / 知识库时一并删除切分块
    client.delete(f"{base}/{doc['id']}", headers=h)
    assert chunks_of(doc["id"]) == []
    client.put(f"{base}/{empty['id']}", headers=h, json={"title": "空文档", "content": "<p>有内容了</p>"})
    assert len(chunks_of(empty["id"])) == 1
    assert client.delete(f"/api/knowledge-bases/{kb_id}", headers=h).status_code == 200
    assert chunks_of(empty["id"]) == []
