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
            for table in ("notifications", "invite_links", "permissions", "documents", "knowledge_bases"):
                conn.execute(text(f"DELETE FROM {table}"))
            conn.execute(text("DELETE FROM users WHERE email LIKE '%@perm.com'"))
            conn.commit()


def login(client, username):
    r = client.post("/api/auth/register", json={"username": username, "email": f"{username}@perm.com", "password": "abc12345"})
    data = r.json()["data"]
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]["id"]


def test_permission_flow(client):
    ho, uid_o = login(client, "powner")
    ha, uid_a = login(client, "palice")
    hb, uid_b = login(client, "pbob")
    hc, uid_c = login(client, "pcarol")

    kb = client.post("/api/knowledge-bases", headers=ho, json={"name": "权限测试库"}).json()["data"]
    assert kb["my_permission"] == "owner"
    kb_url = f"/api/knowledge-bases/{kb['id']}"
    docs_url = f"{kb_url}/documents"
    d1 = client.post(docs_url, headers=ho, json={"title": "文档一", "content": "<p>1</p>"}).json()["data"]
    d2 = client.post(docs_url, headers=ho, json={"title": "文档二", "content": "<p>2</p>"}).json()["data"]

    # 未授权：能看到知识库和文档列表，但打不开文档
    r = client.get(kb_url, headers=ha).json()["data"]
    assert r["my_permission"] is None
    assert client.get("/api/knowledge-bases", headers=ha).json()["data"]["total"] == 1
    assert client.get("/api/knowledge-bases", headers=ha, params={"scope": "mine"}).json()["data"]["total"] == 0
    items = client.get(docs_url, headers=ha).json()["data"]["items"]
    assert len(items) == 2 and all(d["my_permission"] is None and "content" not in d for d in items)
    r = client.get(f"{docs_url}/{d1['id']}", headers=ha)
    assert r.status_code == 403 and r.json()["message"] == "没有该文档的读权限"
    assert client.get(f"{docs_url}/99999", headers=ha).status_code == 404

    # ---------- 邀请：管理者发起，被邀请人接受 ----------
    inv = {"username": "palice", "target_type": "kb", "target_id": kb["id"], "permission_type": "read"}
    assert client.post("/api/permissions/invitations", headers=ha, json=inv).status_code == 403  # 无权管理
    r = client.post("/api/permissions/invitations", headers=ho, json=inv).json()["data"]
    assert r["status"] == "pending" and r["direction"] == "invite" and r["granted_at"] is None
    assert r["user"]["username"] == "palice" and r["initiator"]["username"] == "powner" and r["target_name"] == "权限测试库"
    pid_a = r["id"]
    assert client.post("/api/permissions/invitations", headers=ho, json=inv).json()["code"] == 40040  # 重复邀请
    assert client.get(f"{docs_url}/{d1['id']}", headers=ha).status_code == 403  # 接受前不生效

    mine = client.get("/api/permissions/mine", headers=ha, params={"status": "pending"}).json()["data"]
    assert [p["id"] for p in mine] == [pid_a]
    assert client.post(f"/api/permissions/{pid_a}/accept", headers=hb).status_code == 403  # 只有被邀请人能处理
    r = client.post(f"/api/permissions/{pid_a}/accept", headers=ha).json()["data"]
    assert r["status"] == "accept" and r["granted_at"]
    assert client.post(f"/api/permissions/{pid_a}/accept", headers=ha).json()["code"] == 40040  # 已处理

    # 读权限：能看知识库和全部文档，不能写
    page = client.get("/api/knowledge-bases", headers=ha, params={"scope": "mine"}).json()["data"]
    assert page["total"] == 1 and page["items"][0]["my_permission"] == "read"
    assert client.get(docs_url, headers=ha).json()["data"]["total"] == 2
    assert client.get(f"{docs_url}/{d1['id']}", headers=ha).json()["data"]["my_permission"] == "read"
    assert client.post(docs_url, headers=ha, json={"title": "x"}).status_code == 403
    assert client.put(f"{docs_url}/{d1['id']}", headers=ha, json={"title": "x"}).status_code == 403
    assert client.put(kb_url, headers=ha, json={"name": "改名"}).status_code == 403

    # ---------- 修改权限类型：读 → 写 ----------
    assert client.patch(f"/api/permissions/{pid_a}", headers=ho, json={"permission_type": "write"}).json()["data"]["permission_type"] == "write"
    own = client.post(docs_url, headers=ha, json={"title": "alice 的文档"}).json()["data"]
    assert own["author_id"] == uid_a and own["my_permission"] == "write"
    assert client.put(f"{docs_url}/{d1['id']}", headers=ha, json={"title": "文档一", "content": "<p>改</p>"}).status_code == 200
    assert client.delete(f"{docs_url}/{d1['id']}", headers=ha).status_code == 403  # 删除需要管理权限
    assert client.delete(f"{docs_url}/{own['id']}", headers=ha).status_code == 403  # 自己写的也一样
    assert client.post(f"{docs_url}/images", headers=ha, files={"file": ("a.png", PNG, "image/png")}).status_code == 200

    # ---------- 申请：用户发起，管理者审批（文档级） ----------
    app_doc = {"target_type": "doc", "target_id": d2["id"], "permission_type": "read"}
    r = client.post("/api/permissions/applications", headers=hb, json=app_doc).json()["data"]
    assert r["direction"] == "apply" and r["target_name"] == "文档二" and r["kb_id"] == kb["id"]
    pid_b = r["id"]
    assert [p["id"] for p in client.get("/api/permissions/approvals", headers=ho).json()["data"]] == [pid_b]
    assert client.get("/api/permissions/approvals", headers=ha).json()["data"] == []  # 写权限不能审批
    assert client.post(f"/api/permissions/{pid_b}/accept", headers=ha).status_code == 403
    assert client.post(f"/api/permissions/{pid_b}/accept", headers=hb).status_code == 403  # 不能审批自己的申请
    assert client.post(f"/api/permissions/{pid_b}/accept", headers=ho).json()["data"]["status"] == "accept"

    # 只对部分文档有权限：列表中全部文档可见，只有被授权的文档能打开
    page = client.get("/api/knowledge-bases", headers=hb, params={"scope": "mine"}).json()["data"]
    assert page["total"] == 1 and page["items"][0]["my_permission"] is None
    perms = {d["id"]: d["my_permission"] for d in client.get(docs_url, headers=hb).json()["data"]["items"]}
    assert perms[d2["id"]] == "read" and perms[d1["id"]] is None
    assert client.get(f"{docs_url}/{d2['id']}", headers=hb).status_code == 200
    assert client.put(f"{docs_url}/{d2['id']}", headers=hb, json={"title": "x"}).status_code == 403  # 只读不能编辑
    assert client.delete(f"{docs_url}/{d2['id']}", headers=hb).status_code == 403
    assert client.get(f"{docs_url}/{d1['id']}", headers=hb).status_code == 403
    assert client.post(f"{docs_url}/images", headers=hb, files={"file": ("a.png", PNG, "image/png")}).status_code == 403
    # 已拥有读权限，再次申请读权限：冲突
    assert client.post("/api/permissions/applications", headers=hb, json=app_doc).json()["code"] == 40040

    # ---------- 拒绝后重新申请、撤回 ----------
    app_kb = {"target_type": "kb", "target_id": kb["id"], "permission_type": "write"}
    pid_c = client.post("/api/permissions/applications", headers=hc, json=app_kb).json()["data"]["id"]
    assert client.post(f"/api/permissions/{pid_c}/reject", headers=ho).json()["data"]["status"] == "reject"
    assert client.get(kb_url, headers=hc).json()["data"]["my_permission"] is None
    r = client.post("/api/permissions/applications", headers=hc, json=app_kb).json()["data"]
    assert r["id"] == pid_c and r["status"] == "pending"  # 复用原记录
    assert client.delete(f"/api/permissions/{pid_c}", headers=hc).status_code == 200  # 撤回
    assert client.get("/api/permissions/mine", headers=hc).json()["data"] == []

    # ---------- 管理权限：可改名、可管理授权，不能删除 / 转让知识库 ----------
    client.patch(f"/api/permissions/{pid_a}", headers=ho, json={"permission_type": "manage"})
    assert client.patch(f"/api/permissions/{pid_a}", headers=ha, json={"permission_type": "read"}).status_code == 403  # 不能改自己
    assert client.put(kb_url, headers=ha, json={"name": "权限测试库2"}).status_code == 200
    assert client.delete(f"{docs_url}/{d1['id']}", headers=ha).status_code == 200
    assert client.delete(f"{docs_url}/{own['id']}", headers=ha).status_code == 200
    assert client.delete(kb_url, headers=ha).status_code == 403
    assert client.patch(f"{kb_url}/owner", headers=ha, json={"owner_id": uid_a}).status_code == 403
    members = client.get("/api/permissions", headers=ha, params={"target_type": "kb", "target_id": kb["id"]}).json()["data"]
    assert [m["user_id"] for m in members] == [uid_a]
    inv_c = {"username": "pcarol", "target_type": "doc", "target_id": d2["id"], "permission_type": "write"}
    assert client.post("/api/permissions/invitations", headers=ha, json=inv_c).status_code == 200
    assert client.post("/api/permissions/invitations", headers=ha, json={**inv_c, "username": "palice"}).json()["code"] == 40040
    assert client.post("/api/permissions/invitations", headers=ha, json={**inv_c, "username": "powner"}).json()["code"] == 40040
    assert client.get("/api/permissions", headers=hb, params={"target_type": "doc", "target_id": d2["id"]}).status_code == 403

    # ---------- 放弃权限 ----------
    assert client.delete(f"/api/permissions/{pid_b}", headers=hb).status_code == 200
    assert client.get(f"{docs_url}/{d2['id']}", headers=hb).status_code == 403

    # ---------- 删除文档 / 知识库时清理授权 ----------
    from app.db.session import engine
    from app.mapper import permission_mapper
    from app.models.permission import TargetType

    client.delete(f"{docs_url}/{d2['id']}", headers=ho)
    with engine.connect() as conn:
        assert permission_mapper.select_list(conn, target_type=TargetType.DOC, target_id=d2["id"]) == []
    client.delete(kb_url, headers=ho)
    with engine.connect() as conn:
        assert permission_mapper.select_list(conn, target_type=TargetType.KB, target_id=kb["id"]) == []


def test_upgrade_application(client):
    ho, _ = login(client, "uowner")
    hr, _ = login(client, "ureader")
    kb = client.post("/api/knowledge-bases", headers=ho, json={"name": "追加申请库"}).json()["data"]
    docs_url = f"/api/knowledge-bases/{kb['id']}/documents"
    doc = client.post(docs_url, headers=ho, json={"title": "文档", "content": "<p>1</p>"}).json()["data"]
    doc_url = f"{docs_url}/{doc['id']}"
    apply = lambda ptype: client.post(  # noqa: E731
        "/api/permissions/applications", headers=hr, json={"target_type": "doc", "target_id": doc["id"], "permission_type": ptype}
    ).json()

    # 先获得读权限
    pid_read = apply("read")["data"]["id"]
    client.post(f"/api/permissions/{pid_read}/accept", headers=ho)

    # 追加申请写权限：审批期间仍是读权限
    assert apply("read")["code"] == 40040  # 不能申请已有的权限
    r = apply("write")["data"]
    pid_write = r["id"]
    assert pid_write != pid_read and r["status"] == "pending"
    assert apply("manage")["code"] == 40040  # 同时只能有一个待处理申请
    d = client.get(doc_url, headers=hr).json()["data"]
    assert d["my_permission"] == "read" and d["pending_permission"] == "write"
    assert client.get(docs_url, headers=hr).json()["data"]["items"][0]["pending_permission"] == "write"
    assert client.put(doc_url, headers=hr, json={"title": "x"}).status_code == 403

    # 驳回：保持读权限，可以重新申请（复用被拒绝的记录）
    client.post(f"/api/permissions/{pid_write}/reject", headers=ho)
    d = client.get(doc_url, headers=hr).json()["data"]
    assert d["my_permission"] == "read" and d["pending_permission"] is None
    assert apply("write")["data"]["id"] == pid_write

    # 通过：替换为写权限，旧的读权限记录被删除
    assert client.post(f"/api/permissions/{pid_write}/accept", headers=ho).json()["data"]["permission_type"] == "write"
    assert client.get(doc_url, headers=hr).json()["data"]["my_permission"] == "write"
    assert client.put(doc_url, headers=hr, json={"title": "改", "content": ""}).status_code == 200
    members = client.get("/api/permissions", headers=ho, params={"target_type": "doc", "target_id": doc["id"]}).json()["data"]
    assert [(m["permission_type"], m["status"]) for m in members] == [("write", "accept")]

    # 管理者邀请更高权限同样适用；审批期间管理者直接调高了权限时，接受邀请不会降级
    inv = {"username": "ureader", "target_type": "doc", "target_id": doc["id"], "permission_type": "manage"}
    assert client.post("/api/permissions/invitations", headers=ho, json={**inv, "permission_type": "write"}).json()["code"] == 40040
    pid_inv = client.post("/api/permissions/invitations", headers=ho, json=inv).json()["data"]["id"]
    assert client.get(doc_url, headers=hr).json()["data"]["my_permission"] == "write"
    client.patch(f"/api/permissions/{pid_inv}", headers=ho, json={"permission_type": "read"})
    assert client.post(f"/api/permissions/{pid_inv}/accept", headers=hr).json()["data"]["permission_type"] == "write"
    assert client.get(doc_url, headers=hr).json()["data"]["my_permission"] == "write"

    # 知识库级同样返回审批中的申请
    client.post("/api/permissions/applications", headers=hr, json={"target_type": "kb", "target_id": kb["id"], "permission_type": "read"})
    assert client.get(f"/api/knowledge-bases/{kb['id']}", headers=hr).json()["data"]["pending_permission"] == "read"


def test_permission_notifications(client):
    ho, _ = login(client, "nowner")
    hu, uid_u = login(client, "nuser")
    kb = client.post("/api/knowledge-bases", headers=ho, json={"name": "通知库"}).json()["data"]
    doc = client.post(f"/api/knowledge-bases/{kb['id']}/documents", headers=ho, json={"title": "安装指南"}).json()["data"]
    notices = lambda h=hu: client.get("/api/notifications", headers=h).json()["data"]  # noqa: E731
    latest = lambda: notices()["items"][0]  # noqa: E731

    # 自己的操作（申请、撤回）不产生通知
    app_doc = {"target_type": "doc", "target_id": doc["id"], "permission_type": "read"}
    pid = client.post("/api/permissions/applications", headers=hu, json=app_doc).json()["data"]["id"]
    assert notices()["total"] == 0

    # 申请被同意
    client.post(f"/api/permissions/{pid}/accept", headers=ho)
    n = latest()
    assert n["type"] == "perm_approved" and n["content"] == "同意了你对文档「安装指南」的读权限申请"
    assert n["actor"]["username"] == "nowner" and n["kb_id"] == kb["id"] and n["target_id"] == doc["id"]
    assert n["is_read"] is False and notices()["unread"] == 1

    # 权限被调整（同类型不重复通知）、被撤销
    client.patch(f"/api/permissions/{pid}", headers=ho, json={"permission_type": "write"})
    assert latest()["type"] == "perm_changed" and latest()["content"] == "将你对文档「安装指南」的权限由读调整为写"
    client.patch(f"/api/permissions/{pid}", headers=ho, json={"permission_type": "write"})
    assert notices()["total"] == 2
    client.delete(f"/api/permissions/{pid}", headers=ho)
    assert latest()["type"] == "perm_revoked" and latest()["content"] == "撤销了你对文档「安装指南」的写权限"

    # 邀请、撤回邀请
    inv = {"username": "nuser", "target_type": "kb", "target_id": kb["id"], "permission_type": "manage"}
    pid = client.post("/api/permissions/invitations", headers=ho, json=inv).json()["data"]["id"]
    assert latest()["type"] == "perm_invited" and latest()["content"] == "邀请你获得知识库「通知库」的管理权限"
    client.delete(f"/api/permissions/{pid}", headers=ho)
    assert latest()["type"] == "perm_withdrawn"

    # 申请被驳回
    pid = client.post("/api/permissions/applications", headers=hu, json=app_doc).json()["data"]["id"]
    client.post(f"/api/permissions/{pid}/reject", headers=ho)
    assert latest()["type"] == "perm_rejected"

    # 知识库转让
    client.patch(f"/api/knowledge-bases/{kb['id']}/owner", headers=ho, json={"owner_id": uid_u})
    assert latest()["type"] == "kb_transferred"
    assert notices(ho)["total"] == 0  # 操作人自己不收通知

    # 已读：只能标记自己的通知
    page = notices()
    assert page["unread"] == page["total"] == 7
    first = page["items"][0]["id"]
    assert client.post(f"/api/notifications/{first}/read", headers=ho).status_code == 404
    client.post(f"/api/notifications/{first}/read", headers=hu)
    assert notices()["unread"] == 6
    assert client.get("/api/notifications", headers=hu, params={"unread_only": True}).json()["data"]["total"] == 6
    client.post("/api/notifications/read-all", headers=hu)
    assert notices()["unread"] == 0


def test_invite_link(client):
    ho, uid_o = login(client, "lowner")
    ha, uid_a = login(client, "lalice")
    hb, uid_b = login(client, "lbob")
    hc, uid_c = login(client, "lcarol")

    kb = client.post("/api/knowledge-bases", headers=ho, json={"name": "链接测试库"}).json()["data"]
    docs_url = f"/api/knowledge-bases/{kb['id']}/documents"
    doc = client.post(docs_url, headers=ho, json={"title": "链接文档", "content": "<p>x</p>"}).json()["data"]
    links_url = "/api/permissions/invite-links"
    redeem = lambda token, h: client.post(f"{links_url}/{token}/redeem", headers=h)  # noqa: E731

    # 生成：需要管理权限
    body = {"target_type": "doc", "target_id": doc["id"], "permission_type": "write", "max_uses": 2}
    assert client.post(links_url, headers=ha, json=body).status_code == 403
    link = client.post(links_url, headers=ho, json=body).json()["data"]
    assert link["valid"] and link["use_count"] == 0 and link["max_uses"] == 2 and link["expires_at"]
    assert link["creator"]["username"] == "lowner"
    params = {"target_type": "doc", "target_id": doc["id"]}
    assert client.get(links_url, headers=ha, params=params).status_code == 403
    assert [l["id"] for l in client.get(links_url, headers=ho, params=params).json()["data"]] == [link["id"]]

    # 打开链接：直接获得写权限，返回跳转目标；需要登录
    assert client.post(f"{links_url}/{link['token']}/redeem").status_code == 401
    assert client.put(f"{docs_url}/{doc['id']}", headers=ha, json={"title": "改", "content": "<p>y</p>"}).status_code == 403
    r = redeem(link["token"], ha).json()["data"]
    assert r == {
        "target_type": "doc", "target_id": doc["id"], "target_name": "链接文档", "kb_id": kb["id"],
        "permission_type": "write", "granted": True, "my_permission": "write",
    }
    assert client.put(f"{docs_url}/{doc['id']}", headers=ha, json={"title": "改", "content": "<p>y</p>"}).status_code == 200
    granted = client.get("/api/permissions/mine", headers=ha, params={"status": "accept"}).json()["data"]
    assert len(granted) == 1 and granted[0]["initiator_id"] == uid_o and granted[0]["granted_at"]
    # 生成人收到通知
    notices = client.get("/api/notifications", headers=ho).json()["data"]["items"]
    assert notices[0]["type"] == "link_joined" and notices[0]["actor"]["username"] == "lalice"

    # 再次打开：已拥有，不占用次数；拥有者打开也直接跳转
    r = redeem(link["token"], ha).json()["data"]
    assert r["granted"] is False and r["my_permission"] == "write"
    assert redeem(link["token"], ho).json()["data"]["my_permission"] == "owner"
    assert client.get(links_url, headers=ho, params=params).json()["data"][0]["use_count"] == 1

    # 已有较低权限 + 被覆盖的待处理申请：提升为链接权限，申请被清理
    inv = {"username": "lbob", "target_type": "doc", "target_id": doc["id"], "permission_type": "read"}
    pid = client.post("/api/permissions/invitations", headers=ho, json=inv).json()["data"]["id"]
    client.post(f"/api/permissions/{pid}/accept", headers=hb)
    client.post("/api/permissions/applications", headers=hb, json={**params, "permission_type": "write"})
    assert redeem(link["token"], hb).json()["data"]["granted"] is True
    mine = client.get("/api/permissions/mine", headers=hb).json()["data"]
    assert [(p["status"], p["permission_type"]) for p in mine] == [("accept", "write")]

    # 次数用完
    r = redeem(link["token"], hc)
    assert r.status_code == 400 and r.json()["code"] == 40041
    assert client.get(links_url, headers=ho, params=params).json()["data"][0]["valid"] is False

    # 知识库链接；生成人失去管理权限后链接失效
    inv = {"username": "lalice", "target_type": "kb", "target_id": kb["id"], "permission_type": "manage"}
    pid = client.post("/api/permissions/invitations", headers=ho, json=inv).json()["data"]["id"]
    client.post(f"/api/permissions/{pid}/accept", headers=ha)
    kb_link = client.post(links_url, headers=ha, json={"target_type": "kb", "target_id": kb["id"], "permission_type": "read",
                                                       "expires_in_days": None}).json()["data"]
    assert kb_link["expires_at"] is None
    client.delete(f"/api/permissions/{pid}", headers=ho)
    r = redeem(kb_link["token"], hc)
    assert r.status_code == 400 and r.json()["code"] == 40041

    # 撤销
    kb_link = client.post(links_url, headers=ho, json={"target_type": "kb", "target_id": kb["id"], "permission_type": "read"}).json()["data"]
    assert client.delete(f"{links_url}/{kb_link['id']}", headers=ha).status_code == 403
    assert client.delete(f"{links_url}/{kb_link['id']}", headers=ho).status_code == 200
    assert redeem(kb_link["token"], hc).status_code == 404

    # 删除文档 / 知识库时一并清理链接
    kb_link = client.post(links_url, headers=ho, json={"target_type": "kb", "target_id": kb["id"], "permission_type": "read"}).json()["data"]
    client.delete(f"{docs_url}/{doc['id']}", headers=ho)
    assert client.get(links_url, headers=ho, params=params).status_code == 404
    client.delete(f"/api/knowledge-bases/{kb['id']}", headers=ho)
    assert redeem(kb_link["token"], hc).status_code == 404
