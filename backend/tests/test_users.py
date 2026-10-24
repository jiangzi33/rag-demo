import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["UPLOAD_DIR"] = str(_tmp / "uploads")
os.environ["ADMIN_USERNAMES"] = "admin"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def register(client, username, email, password="abc12345", **extra):
    return client.post("/api/auth/register", json={"username": username, "email": email, "password": password, **extra})


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_and_login(client):
    r = register(client, "alice", "Alice@Example.com", nickname="爱丽丝")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["user"]["email"] == "alice@example.com"
    assert data["user"]["nickname"] == "爱丽丝"
    assert data["user"]["is_vip"] is False and data["user"]["status"] == 1
    assert "password" not in data["user"]

    # 默认昵称 = 用户名
    assert register(client, "bobby", "bob@example.com").json()["data"]["user"]["nickname"] == "bobby"

    # 用户名 / 邮箱重复
    assert register(client, "alice", "x@example.com").json()["code"] == 40010
    assert register(client, "alice2", "alice@example.com").json()["code"] == 40011

    # 参数校验
    assert register(client, "1bad", "c@example.com").status_code == 422
    assert register(client, "carol", "c@example.com", password="onlyletters").status_code == 422

    # 用户名 / 邮箱登录
    assert client.post("/api/auth/login", json={"account": "alice", "password": "abc12345"}).status_code == 200
    assert client.post("/api/auth/login", json={"account": "ALICE@example.com", "password": "abc12345"}).status_code == 200
    r = client.post("/api/auth/login", json={"account": "alice", "password": "wrong123"})
    assert r.status_code == 401 and r.json()["code"] == 40012


def test_profile_flow(client):
    token = client.post("/api/auth/login", json={"account": "alice", "password": "abc12345"}).json()["data"]["access_token"]
    h = auth_header(token)

    assert client.get("/api/users/me").status_code == 401
    assert client.get("/api/users/me", headers=auth_header("bad.token")).status_code == 401
    assert client.get("/api/users/me", headers=h).json()["data"]["username"] == "alice"

    r = client.put("/api/users/me", headers=h, json={"nickname": "Alice2", "email": "new@example.com"})
    assert r.json()["data"]["nickname"] == "Alice2"
    assert client.put("/api/users/me", headers=h, json={"email": "bob@example.com"}).json()["code"] == 40011

    r = client.put("/api/users/me/password", headers=h, json={"old_password": "wrong", "new_password": "newpass123"})
    assert r.json()["code"] == 40013
    r = client.put("/api/users/me/password", headers=h, json={"old_password": "abc12345", "new_password": "newpass123"})
    assert r.status_code == 200
    assert client.post("/api/auth/login", json={"account": "alice", "password": "newpass123"}).status_code == 200

    # 头像上传：替换时删除旧文件
    r1 = client.post("/api/users/me/avatar", headers=h, files={"file": ("a.png", PNG, "image/png")})
    url1 = r1.json()["data"]["avatar"]
    assert client.get(url1).status_code == 200
    url2 = client.post("/api/users/me/avatar", headers=h, files={"file": ("b.png", PNG, "image/png")}).json()["data"]["avatar"]
    assert url1 != url2 and client.get(url1).status_code == 404

    r = client.post("/api/users/me/avatar", headers=h, files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.json()["code"] == 40020
    big = b"0" * (2 * 1024 * 1024 + 10)
    assert client.post("/api/users/me/avatar", headers=h, files={"file": ("a.png", big, "image/png")}).json()["code"] == 40020


def test_admin_flow(client):
    admin = register(client, "admin", "admin@example.com").json()["data"]
    assert admin["user"]["is_admin"] is True
    ah = auth_header(admin["access_token"])
    bob = client.post("/api/auth/login", json={"account": "bobby", "password": "abc12345"}).json()["data"]
    bh = auth_header(bob["access_token"])
    bob_id = bob["user"]["id"]

    # 非管理员无权限
    assert client.get("/api/admin/users", headers=bh).status_code == 403

    page = client.get("/api/admin/users", headers=ah, params={"page": 1, "size": 2}).json()["data"]
    assert page["total"] == 3 and len(page["items"]) == 2
    assert client.get("/api/admin/users", headers=ah, params={"keyword": "bob"}).json()["data"]["total"] == 1
    assert client.get("/api/admin/users", headers=ah, params={"keyword": "%"}).json()["data"]["total"] == 0

    # VIP
    assert client.patch(f"/api/admin/users/{bob_id}/vip", headers=ah, json={"is_vip": True}).json()["data"]["is_vip"]
    assert client.get("/api/admin/users", headers=ah, params={"is_vip": True}).json()["data"]["total"] == 1

    # 禁用后：已有 token 立即失效、无法登录
    client.patch(f"/api/admin/users/{bob_id}/status", headers=ah, json={"status": 0})
    assert client.get("/api/users/me", headers=bh).status_code == 403
    assert client.post("/api/auth/login", json={"account": "bobby", "password": "abc12345"}).status_code == 403
    client.patch(f"/api/admin/users/{bob_id}/status", headers=ah, json={"status": 1})
    assert client.get("/api/users/me", headers=bh).status_code == 200

    # 不能改自己的状态 / 非法状态 / 不存在的用户
    assert client.patch(f"/api/admin/users/{admin['user']['id']}/status", headers=ah, json={"status": 0}).status_code == 403
    assert client.patch(f"/api/admin/users/{bob_id}/status", headers=ah, json={"status": 9}).status_code == 422
    assert client.patch("/api/admin/users/99999/vip", headers=ah, json={"is_vip": True}).status_code == 404


def test_mapper_delete_and_whitelist(client):
    from app.db.session import engine
    from app.mapper import user_mapper

    with engine.connect() as conn:
        uid = user_mapper.insert(conn, "tmpuser", "hash", "tmp@example.com", "tmp")
        conn.commit()
        assert user_mapper.select_by_id(conn, uid).username == "tmpuser"
        with pytest.raises(ValueError):
            user_mapper.update_selective(conn, uid, username="hacker")  # 不在白名单的列
        assert user_mapper.update_selective(conn, uid) == 0  # 无字段时不执行 SQL
        assert user_mapper.delete_by_id(conn, uid) == 1
        conn.commit()
        assert user_mapper.select_by_id(conn, uid) is None


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200 and '<div id="app">' in r.text
