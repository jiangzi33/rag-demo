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


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
        # 与 test_users 共用同一个测试库，结束后清理本模块的数据
        from sqlalchemy import text

        from app.db.session import engine

        with engine.connect() as conn:
            conn.execute(text("DELETE FROM knowledge_bases"))
            conn.execute(text("DELETE FROM users WHERE email LIKE '%@kb.com'"))
            conn.commit()


def login(client, username):
    """注册（已存在则登录），返回 (请求头, 用户 id)。"""
    r = client.post("/api/auth/register", json={"username": username, "email": f"{username}@kb.com", "password": "abc12345"})
    if r.json()["code"] != 0:
        r = client.post("/api/auth/login", json={"account": username, "password": "abc12345"})
    data = r.json()["data"]
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]["id"]


def test_knowledge_base_flow(client, monkeypatch):
    h1, uid1 = login(client, "kbowner")
    h2, uid2 = login(client, "kbother")

    assert client.get("/api/knowledge-bases").status_code == 401

    # 创建：创建者即管理员，名称去首尾空格
    r = client.post("/api/knowledge-bases", headers=h1, json={"name": "  产品文档 "})
    assert r.status_code == 200, r.text
    kb = r.json()["data"]
    assert kb["name"] == "产品文档" and kb["owner_id"] == uid1
    assert kb["created_at"] and kb["updated_at"]

    # 同一管理员下重名不允许，不同管理员可以重名
    assert client.post("/api/knowledge-bases", headers=h1, json={"name": "产品文档"}).json()["code"] == 40030
    assert client.post("/api/knowledge-bases", headers=h2, json={"name": "产品文档"}).status_code == 200
    assert client.post("/api/knowledge-bases", headers=h1, json={"name": "   "}).status_code == 422

    client.post("/api/knowledge-bases", headers=h1, json={"name": "技术手册"})
    r = client.get("/api/knowledge-bases", headers=h1, params={"keyword": "手册"}).json()["data"]
    assert r["total"] == 1 and r["items"][0]["name"] == "技术手册"
    assert client.get("/api/knowledge-bases", headers=h1).json()["data"]["total"] == 2

    # 他人无权查看 / 修改 / 删除
    url = f"/api/knowledge-bases/{kb['id']}"
    assert client.get(url, headers=h2).status_code == 404
    assert client.put(url, headers=h2, json={"name": "x"}).status_code == 404
    assert client.delete(url, headers=h2).status_code == 404

    # 改名
    r = client.put(url, headers=h1, json={"name": "产品文档V2"}).json()["data"]
    assert r["name"] == "产品文档V2" and r["created_at"] == kb["created_at"]
    assert client.put(url, headers=h1, json={"name": "技术手册"}).json()["code"] == 40030

    # 转让管理员：目标不存在 404；转让后原管理员失去权限
    assert client.patch(f"{url}/owner", headers=h1, json={"owner_id": 99999}).status_code == 404
    r = client.patch(f"{url}/owner", headers=h1, json={"owner_id": uid2}).json()["data"]
    assert r["owner_id"] == uid2
    assert client.get(url, headers=h1).status_code == 404
    assert client.get(url, headers=h2).status_code == 200

    # 系统管理员可管理任意知识库
    monkeypatch.setattr(settings, "ADMIN_USERNAMES", "kbadmin")
    ha, _ = login(client, "kbadmin")
    assert client.get(url, headers=ha).status_code == 200

    assert client.delete(url, headers=h2).status_code == 200
    assert client.get(url, headers=h2).status_code == 404
