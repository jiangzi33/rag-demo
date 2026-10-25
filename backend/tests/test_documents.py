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

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
        # 与其他测试模块共用同一个测试库，结束后清理本模块的数据
        from sqlalchemy import text

        from app.db.session import engine

        with engine.connect() as conn:
            conn.execute(text("DELETE FROM documents"))
            conn.execute(text("DELETE FROM knowledge_bases"))
            conn.execute(text("DELETE FROM users WHERE email LIKE '%@doc.com'"))
            conn.commit()


def login(client, username):
    r = client.post("/api/auth/register", json={"username": username, "email": f"{username}@doc.com", "password": "abc12345"})
    data = r.json()["data"]
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]["id"]


def test_document_flow(client):
    h1, uid1 = login(client, "docowner")
    h2, _ = login(client, "docother")
    kb_id = client.post("/api/knowledge-bases", headers=h1, json={"name": "文档测试库"}).json()["data"]["id"]
    other_kb = client.post("/api/knowledge-bases", headers=h2, json={"name": "别人的库"}).json()["data"]["id"]
    base = f"/api/knowledge-bases/{kb_id}/documents"

    # 创建：当前用户为作者，标题去首尾空格
    html = '<p style="text-align: center;"><span style="color: rgb(225, 60, 57); font-size: 24px; font-family: 楷体;">安装依赖</span></p>'
    r = client.post(base, headers=h1, json={"title": "  安装指南 ", "content": html})
    assert r.status_code == 200, r.text
    doc = r.json()["data"]
    assert doc["title"] == "安装指南" and doc["kb_id"] == kb_id
    assert doc["author_id"] == uid1 and doc["author"]["username"] == "docowner"
    # 样式按原格式保留（编辑器再次加载时按原值匹配字体列表）
    assert doc["content"] == html and doc["published_at"] and doc["updated_at"]

    # 富文本过滤：去掉脚本、事件属性、javascript: 链接
    evil = (
        '<p>ok</p><img src="x" onerror="alert(1)"><script>alert(2)</script><a href="javascript:alert(3)">x</a>'
        '<p style="position: fixed; color: red; background-color: url(javascript:x)">s</p>'
    )
    cleaned = client.post(base, headers=h1, json={"title": "xss", "content": evil}).json()["data"]
    assert "onerror" not in cleaned["content"] and "<script" not in cleaned["content"]
    assert "javascript:" not in cleaned["content"] and "<p>ok</p>" in cleaned["content"]
    assert '<p style="color: red;">s</p>' in cleaned["content"]
    client.delete(f"{base}/{cleaned['id']}", headers=h1)

    assert client.post(base, headers=h1, json={"title": "   "}).status_code == 422
    client.post(base, headers=h1, json={"title": "常见问题"})

    # 列表不返回正文，支持标题搜索
    page = client.get(base, headers=h1).json()["data"]
    assert page["total"] == 2 and "content" not in page["items"][0]
    assert page["items"][0]["author"]["nickname"] == "docowner"
    assert client.get(base, headers=h1, params={"keyword": "安装"}).json()["data"]["total"] == 1

    # 编辑：作者、发布日期不变
    url = f"{base}/{doc['id']}"
    r = client.put(url, headers=h1, json={"title": "安装指南 v2", "content": "<p>新正文</p>"}).json()["data"]
    assert r["title"] == "安装指南 v2" and r["content"] == "<p>新正文</p>"
    assert r["author_id"] == uid1 and r["published_at"] == doc["published_at"]
    assert client.get(url, headers=h1).json()["data"]["content"] == "<p>新正文</p>"

    # 正文图片：按文件头识别格式，归属于知识库
    r = client.post(f"{base}/images", headers=h1, files={"file": ("a.png", PNG, "image/png")})
    img_url = r.json()["data"]["url"]
    assert img_url.startswith(f"/uploads/doc-images/{kb_id}/") and img_url.endswith(".png")
    assert client.get(img_url).content == PNG
    fake = client.post(f"{base}/images", headers=h1, files={"file": ("a.png", b"<svg onload=alert(1)>", "image/png")})
    assert fake.json()["code"] == 40020
    assert client.post(f"{base}/images", headers=h2, files={"file": ("a.png", PNG, "image/png")}).status_code == 403

    # 他人能看到文档列表，但不能打开 / 编辑；文档不属于路径中的知识库时 404
    assert client.get(base, headers=h2).json()["data"]["total"] == 2
    assert client.get(url, headers=h2).status_code == 403
    assert client.put(url, headers=h2, json={"title": "x"}).status_code == 403
    assert client.delete(url, headers=h2).status_code == 403
    assert client.get(f"/api/knowledge-bases/{other_kb}/documents/{doc['id']}", headers=h2).status_code == 404

    # 删除文档
    assert client.delete(url, headers=h1).status_code == 200
    assert client.get(url, headers=h1).status_code == 404
    assert client.get(base, headers=h1).json()["data"]["total"] == 1

    # 删除知识库时级联删除其下文档
    left_id = client.get(base, headers=h1).json()["data"]["items"][0]["id"]
    assert client.delete(f"/api/knowledge-bases/{kb_id}", headers=h1).status_code == 200
    from app.db.session import engine
    from app.mapper import document_mapper

    with engine.connect() as conn:
        assert document_mapper.select_by_id(conn, left_id) is None
    # 知识库的正文图片目录一并删除
    assert client.get(img_url).status_code == 404
