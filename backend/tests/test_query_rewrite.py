import pytest

from app.core.config import settings
from app.rag.llm import ChatError
from app.services import query_rewrite_service as rw
from app.services.query_rewrite_service import RewrittenQuery


class FakeChat:
    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error
        self.calls: list[dict] = []

    def complete(self, messages, model=None, timeout=None):
        self.calls.append({"messages": messages, "model": model, "timeout": timeout})
        if self.error:
            raise self.error
        return self.reply


def test_parse():
    assert rw.parse('{"query": "如何重置密码？", "keywords": ["密码", "找回密码"]}') == RewrittenQuery("如何重置密码？", ["密码", "找回密码"])
    # 代码块包裹、前后有多余文字、换行折叠
    assert rw.parse('好的：\n```json\n{"query": "如何\\n 重置密码？"}\n```').query == "如何 重置密码？"
    # 关键词去空、去重、最多 5 个；类型不对时忽略
    r = rw.parse('{"query": "q", "keywords": [" a ", "", "a", 1, "b", "c", "d", "e", "f"]}')
    assert r.keywords == ["a", "b", "c", "d", "e"]
    assert rw.parse('{"query": "q", "keywords": "a,b"}').keywords == []
    for bad in ("", "无法改写", '{"keywords": ["a"]}', '{"query": "  "}', '{"query": 1}', "{bad json}", '["q"]'):
        with pytest.raises(ValueError):
            rw.parse(bad)


def test_parse_truncates_query(monkeypatch):
    monkeypatch.setattr(settings, "RAG_QUESTION_MAX_LENGTH", 5)
    assert rw.parse('{"query": "1234567"}').query == "12345"


def test_keyword_text():
    r = RewrittenQuery("Windows 如何安装？", ["Windows", "安装"])
    assert r.keyword_text("那 win 呢") == "那 win 呢 Windows 如何安装？ Windows 安装"
    assert RewrittenQuery("q").keyword_text("q") == "q"


def test_build_messages(monkeypatch):
    monkeypatch.setattr(settings, "QA_HISTORY_MESSAGES", 2)
    monkeypatch.setattr(settings, "QA_REWRITE_HISTORY_CHARS", 4)
    history = [{"role": "user", "content": "旧问题"}, {"role": "assistant", "content": "旧回答"},
               {"role": "user", "content": "Linux 怎么安装？"}, {"role": "assistant", "content": "执行\n install.sh 即可"}]
    messages = rw.build_messages("那 Windows 呢？", history)
    assert messages[0] == {"role": "system", "content": rw.REWRITE_PROMPT}
    # 只带最近的历史，助手的回答截断
    assert messages[1]["content"] == "对话历史：\n用户：Linux 怎么安装？\n助手：执行 i…\n\n最新问题：那 Windows 呢？"
    assert rw.build_messages("q", [])[1]["content"] == "最新问题：q"


def test_rewrite(monkeypatch):
    monkeypatch.setattr(settings, "QA_REWRITE_MODEL", "mini")
    monkeypatch.setattr(settings, "QA_REWRITE_TIMEOUT", 3)
    chat = FakeChat('{"query": "Windows 如何安装？", "keywords": ["安装"]}')
    assert rw.rewrite(chat, "那 win 呢", []) == RewrittenQuery("Windows 如何安装？", ["安装"])
    assert chat.calls[0]["model"] == "mini" and chat.calls[0]["timeout"] == 3
    # 接口报错、格式不对时返回 None
    assert rw.rewrite(FakeChat(error=ChatError("timeout")), "q", []) is None
    assert rw.rewrite(FakeChat("这个问题很清晰"), "q", []) is None
