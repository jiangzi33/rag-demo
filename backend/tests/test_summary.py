import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import documents
from app.core.config import settings
from app.db.session import engine
from app.main import app
from app.rag.llm import ChatError, OpenAIChatClient
from app.rag.vector_store import SearchHit
from app.services import summary_service
from app.services.retrieval_service import Retriever
from tests.test_qa import events
from tests.test_retrieval_service import FakeEmbedder, FakeStore

CONTENT = "<h2>安装</h2><p>执行 ./install.sh 完成安装。</p><h2>配置</h2><p>修改 config.yaml 中的端口。</p>"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM document_summaries"))
            conn.execute(text("DELETE FROM document_chunks"))
            conn.execute(text("DELETE FROM documents"))
            conn.execute(text("DELETE FROM knowledge_bases"))
            conn.execute(text("DELETE FROM users WHERE email LIKE '%@summary.com'"))
            conn.commit()


def register(client, name):
    r = client.post("/api/auth/register", json={"username": name, "email": f"{name}@summary.com", "password": "abc12345"})
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture(scope="module")
def setup(client):
    h = register(client, "summary")
    kb = client.post("/api/knowledge-bases", headers=h, json={"name": "摘要测试库"}).json()["data"]["id"]
    doc = client.post(f"/api/knowledge-bases/{kb}/documents", headers=h, json={"title": "部署手册", "content": CONTENT}).json()["data"]["id"]
    with engine.connect() as conn:
        chunk_ids = [r[0] for r in conn.execute(text("SELECT id FROM document_chunks WHERE doc_id = :d ORDER BY chunk_index"), {"d": doc})]
    return h, kb, doc, chunk_ids


class FakeChat:
    model = "gpt-test"

    def __init__(self, parts=("文档介绍安装和配置", "。执行 ./install.sh [1]"), fail=False, points="- 要点 [1]"):
        self.parts, self.fail, self.points = parts, fail, points
        self.calls: list[list[dict]] = []
        self.map_calls: list[list[dict]] = []

    def complete(self, messages, model=None, timeout=None):
        self.map_calls.append(messages)
        return self.points

    def stream(self, messages):
        self.calls.append(messages)
        yield from self.parts
        if self.fail:
            raise ChatError("连接中断")


def url(kb, doc):
    return f"/api/knowledge-bases/{kb}/documents/{doc}/summary"


def test_batches():
    assert summary_service.batches(["aa", "bb", "cccc", "d"], 4) == [["aa", "bb"], ["cccc"], ["d"]]
    assert summary_service.batches(["toolong"], 3) == [["toolong"]]  # 单条超长时独占一批
    assert summary_service.batches([], 3) == []


def test_full_summary(client, setup, monkeypatch):
    h, kb, doc, chunk_ids = setup
    chat = FakeChat()
    monkeypatch.setattr(documents, "get_chat_client", lambda: chat)

    assert client.post(url(kb, doc)).status_code == 401
    r = client.post(url(kb, doc), headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = events(r)
    assert ev[0]["type"] == "sources" and ev[0]["mode"] == "full" and ev[0]["stale"] is False and ev[0]["cached"] is False
    assert [s["chunk_id"] for s in ev[0]["sources"]] == chunk_ids and len(chunk_ids) == 2
    assert [e["text"] for e in ev[1:-1]] == list(chat.parts) and ev[-1] == {"type": "done"}
    assert chat.map_calls == []  # 短文档一次生成
    system, user = chat.calls[0]
    assert system["content"] == summary_service.SYSTEM_PROMPT
    assert "《部署手册》" in user["content"] and "[1] 安装\n执行 ./install.sh 完成安装。" in user["content"]
    assert "[2] 配置\n" in user["content"]


def test_map_reduce(client, setup, monkeypatch):
    h, kb, doc, _ = setup
    monkeypatch.setattr(settings, "SUMMARY_DIRECT_CHARS", 20)
    monkeypatch.setattr(settings, "SUMMARY_BATCH_CHARS", 10)
    chat = FakeChat(points="- 要点 [1]")
    monkeypatch.setattr(documents, "get_chat_client", lambda: chat)
    ev = events(client.post(url(kb, doc), headers=h, json={"refresh": True}))
    assert ev[0]["mode"] == "map_reduce"
    progress = [e for e in ev if e["type"] == "progress"]
    assert progress[0] == {"type": "progress", "done": 0, "total": 2} and progress[-1] == {"type": "progress", "done": 2, "total": 2}
    assert len(chat.map_calls) == 2 and "第 1/2 部分" in chat.map_calls[0][0]["content"]
    assert "[1] 安装" in chat.map_calls[0][1]["content"] and "[2] 配置" in chat.map_calls[1][1]["content"]
    # 合并阶段拿到的是各批要点，编号保留
    final = chat.calls[0][1]["content"]
    assert "各部分的要点" in final and "- 要点 [1]\n\n- 要点 [1]" in final and "执行 ./install.sh" not in final
    assert ev[-1] == {"type": "done"}


def test_focus_searches_within_doc(client, setup, monkeypatch):
    h, kb, doc, chunk_ids = setup
    monkeypatch.setattr(settings, "EMBEDDING_ENABLED", True)
    hits = [
        SearchHit(chunk_ids[1], doc, kb, 1, "配置", "修改 config.yaml 中的端口。", 0.9),
        SearchHit(chunk_ids[0], doc, kb, 0, "安装", "执行 ./install.sh 完成安装。", 0.5),
        SearchHit(999, doc + 1, kb, 0, "", "其他文档", 0.95),
    ]
    store = FakeStore(hits=hits, keyword_hits=hits)
    monkeypatch.setattr(summary_service, "get_retriever", lambda: Retriever(FakeEmbedder(), store))
    chat = FakeChat()
    monkeypatch.setattr(documents, "get_chat_client", lambda: chat)

    ev = events(client.post(url(kb, doc), headers=h, json={"focus": " 端口 配置 "}))
    assert store.searches[0]["kb_ids"] == [] and store.searches[0]["doc_ids"] == [doc]  # 只在这篇文档内检索
    assert ev[0]["mode"] == "focus"
    assert [s["chunk_id"] for s in ev[0]["sources"]] == chunk_ids  # 按文档顺序，不按得分
    assert "用户只关心：端口 配置" in chat.calls[0][0]["content"]

    store.hits = store.keyword_hits = []
    ev = events(client.post(url(kb, doc), headers=h, json={"focus": "无关"}))
    assert ev[0]["sources"] == [] and ev[1]["text"] == summary_service.NO_FOCUS_ANSWER
    assert len(chat.calls) == 1  # 没有片段时不调用大模型


def test_stale_chunks_use_current_content(client, setup, monkeypatch):
    h, kb, _, _ = setup
    doc = client.post(f"/api/knowledge-bases/{kb}/documents", headers=h, json={"title": "旧", "content": "<p>旧内容</p>"}).json()["data"]["id"]
    client.put(f"/api/knowledge-bases/{kb}/documents/{doc}", headers=h, json={"title": "新", "content": "<p>新内容</p>"})
    chat = FakeChat()
    monkeypatch.setattr(documents, "get_chat_client", lambda: chat)
    ev = events(client.post(url(kb, doc), headers=h))
    assert ev[0]["stale"] is True and [s["chunk_id"] for s in ev[0]["sources"]] == [0]
    assert "新内容" in chat.calls[0][1]["content"] and "旧内容" not in chat.calls[0][1]["content"]


def test_errors(client, setup, monkeypatch):
    h, kb, doc, _ = setup
    # 空文档不调用大模型
    empty = client.post(f"/api/knowledge-bases/{kb}/documents", headers=h, json={"title": "空"}).json()["data"]["id"]
    chat = FakeChat()
    monkeypatch.setattr(documents, "get_chat_client", lambda: chat)
    ev = events(client.post(url(kb, empty), headers=h))
    assert ev[1]["text"] == summary_service.EMPTY_ANSWER and chat.calls == []

    # 生成中途出错：已输出的部分保留
    monkeypatch.setattr(documents, "get_chat_client", lambda: FakeChat(parts=("部分",), fail=True))
    ev = events(client.post(url(kb, doc), headers=h, json={"refresh": True}))
    assert [e["type"] for e in ev] == ["sources", "delta", "error"] and "连接中断" in ev[-1]["message"]

    # 没有读权限 / 文档不存在 / 未配置大模型：流式响应开始前返回 JSON
    other = register(client, "summary2")
    assert client.post(url(kb, doc), headers=other).status_code == 403
    assert client.post(url(kb, 999999), headers=h).status_code == 404
    monkeypatch.setattr(documents, "get_chat_client", lambda: OpenAIChatClient("http://x", "", "m"))
    r = client.post(url(kb, doc), headers=h, json={"refresh": True})
    assert r.status_code == 503 and "OPENAI_API_KEY" in r.json()["message"]
    assert client.post(url(kb, doc), headers=h, json={"focus": "x" * 201}).status_code == 422


def saved(doc):
    with engine.connect() as conn:
        return conn.execute(text("SELECT focus, summary, mode FROM document_summaries WHERE doc_id = :d ORDER BY focus"), {"d": doc}).all()


def test_summary_is_saved_and_reused(client, setup, monkeypatch):
    h, kb, _, _ = setup
    doc = client.post(f"/api/knowledge-bases/{kb}/documents", headers=h, json={"title": "缓存", "content": CONTENT}).json()["data"]["id"]
    chat = FakeChat(parts=("第一版", " [1]"))
    monkeypatch.setattr(documents, "get_chat_client", lambda: chat)
    first = events(client.post(url(kb, doc), headers=h))
    assert saved(doc) == [("", "第一版 [1]", "full")]

    # 文档没变：直接返回保存的摘要，不调用大模型（未配置大模型也可以）
    monkeypatch.setattr(documents, "get_chat_client", lambda: OpenAIChatClient("http://x", "", "m"))
    ev = events(client.post(url(kb, doc), headers=h, json={"focus": "  "}))  # 空白关注点等同全文
    assert ev[0]["cached"] is True and ev[0]["model"] and ev[0]["created_at"]
    assert [s["chunk_id"] for s in ev[0]["sources"]] == [s["chunk_id"] for s in first[0]["sources"]]
    assert ev[0]["sources"][0]["content"] == "" and ev[0]["sources"][0]["heading"] == "安装"  # 不保存片段正文
    assert ev[1:] == [{"type": "delta", "text": "第一版 [1]"}, {"type": "done"}]

    # 重新生成：覆盖旧摘要；关注点各自保存一条
    chat = FakeChat(parts=("第二版",))
    monkeypatch.setattr(documents, "get_chat_client", lambda: chat)
    ev = events(client.post(url(kb, doc), headers=h, json={"refresh": True}))
    assert ev[0]["cached"] is False and len(chat.calls) == 1
    assert saved(doc) == [("", "第二版", "full")]

    # 文档编辑后指纹变化，重新生成
    client.put(f"/api/knowledge-bases/{kb}/documents/{doc}", headers=h, json={"title": "缓存", "content": "<p>改过的内容</p>"})
    ev = events(client.post(url(kb, doc), headers=h))
    assert ev[0]["cached"] is False and ev[0]["stale"] is True and len(chat.calls) == 2

    # 中途出错不保存，旧摘要保留
    monkeypatch.setattr(documents, "get_chat_client", lambda: FakeChat(parts=("半截",), fail=True))
    events(client.post(url(kb, doc), headers=h, json={"refresh": True}))
    assert saved(doc) == [("", "第二版", "full")]

    # 删除文档时一起删除摘要
    client.delete(f"/api/knowledge-bases/{kb}/documents/{doc}", headers=h)
    assert saved(doc) == []


def test_focus_summaries_saved_separately(client, setup, monkeypatch):
    h, kb, _, _ = setup
    doc = client.post(f"/api/knowledge-bases/{kb}/documents", headers=h, json={"title": "关注点", "content": CONTENT}).json()["data"]["id"]
    monkeypatch.setattr(settings, "EMBEDDING_ENABLED", True)
    with engine.connect() as conn:
        first_chunk = conn.execute(text("SELECT id FROM document_chunks WHERE doc_id = :d ORDER BY chunk_index"), {"d": doc}).scalar()
    hits = [SearchHit(first_chunk, doc, kb, 0, "安装", "执行 ./install.sh 完成安装。", 0.9)]
    store = FakeStore(hits=hits, keyword_hits=hits)
    monkeypatch.setattr(summary_service, "get_retriever", lambda: Retriever(FakeEmbedder(), store))
    chat = FakeChat(parts=("安装摘要",))
    monkeypatch.setattr(documents, "get_chat_client", lambda: chat)

    events(client.post(url(kb, doc), headers=h, json={"focus": "安装  步骤"}))
    ev = events(client.post(url(kb, doc), headers=h, json={"focus": " 安装 步骤"}))  # 空白折叠后是同一个关注点
    assert ev[0]["cached"] is True and ev[0]["mode"] == "focus" and len(chat.calls) == 1 and len(store.searches) == 1
    assert saved(doc) == [("安装 步骤", "安装摘要", "focus")]

    # 删除知识库时一起删除摘要
    other_kb = client.post("/api/knowledge-bases", headers=h, json={"name": "待删除库"}).json()["data"]["id"]
    other = client.post(f"/api/knowledge-bases/{other_kb}/documents", headers=h, json={"title": "x", "content": CONTENT}).json()["data"]["id"]
    events(client.post(url(other_kb, other), headers=h))
    assert len(saved(other)) == 1
    client.delete(f"/api/knowledge-bases/{other_kb}", headers=h)
    assert saved(other) == []
