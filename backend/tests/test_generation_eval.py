import json

import pytest

from app.core.config import settings
from app.services import generation_eval_service as gen
from app.services.qa_service import NO_RESULT_ANSWER
from app.services.retrieval_eval_service import EvalCase
from tests.test_retrieval_eval import FakeChat, FakeRetriever, _chunk


class RoutedChat(FakeChat):
    """按系统提示词区分生成 / 打分请求，记录打分用的模型。"""
    model = "gen-model"

    def __init__(self, answers, faithful, quality):
        super().__init__([])
        self.answers, self.faithful, self.quality, self.models = list(answers), list(faithful), list(quality), []

    def complete(self, messages, model=None, timeout=None):
        self.calls.append(messages)
        system = messages[0]["content"]
        if system == gen.FAITHFULNESS_PROMPT:
            self.models.append(model)
            return json.dumps(self.faithful.pop(0), ensure_ascii=False)
        if system == gen.QUALITY_PROMPT:
            return json.dumps(self.quality.pop(0), ensure_ascii=False)
        return self.answers.pop(0)


def test_score():
    assert gen._score(5) == 1.0 and gen._score(1) == 0.0 and gen._score(3) == 0.5
    assert gen._score(9) == 1.0  # 超出范围截断
    assert gen._score(None) is None and gen._score("5") is None and gen._score(True) is None


def test_judge_faithfulness():
    case = EvalCase("a", "q", [1], [1])
    chat = RoutedChat([], [{"claims": [
        {"claim": "A", "verdict": "supported", "sources": [1]},
        {"claim": "B", "verdict": "Unsupported"},
    ]}], [])
    out = gen.judge_faithfulness(chat, case, "A [1]。B [3]。", [_chunk(1), _chunk(2)])
    assert out["metrics"] == {"citation_valid": 0.5, "faithfulness": 0.5, "hallucination": 1.0}
    assert "[2] 《标题》\n片段2" in chat.calls[0][1]["content"]

    chat = RoutedChat([], [{"claims": []}], [])  # 没有事实陈述（如只说没找到）时不计忠实度
    assert gen.judge_faithfulness(chat, case, "没有找到", [_chunk(1)])["metrics"] == {}


def test_judge_quality():
    case = EvalCase("a", "q", [1], [1], reference_answer="参考", key_points=["p1", "p2", "p3"])
    chat = RoutedChat([], [], [{"relevance": 5, "correctness": 2, "covered": [1, 3, 3, 9], "errors": ["错"], "refused": False}])
    out = gen.judge_quality(chat, case, "回答")
    assert out["metrics"] == {"refused": 0.0, "relevance": 1.0, "correctness": 0.25, "completeness": pytest.approx(2 / 3)}
    assert out["missing_points"] == ["p2"] and out["errors"] == ["错"]
    assert "参考答案：\n参考" in chat.calls[0][1]["content"] and "2. p2" in chat.calls[0][1]["content"]

    # 没有参考答案时只有相关性
    chat = RoutedChat([], [], [{"relevance": 3, "correctness": 5, "covered": [1], "refused": True}])
    out = gen.judge_quality(chat, EvalCase("b", "q", [1], [1]), "没有找到")
    assert out["metrics"] == {"refused": 1.0, "relevance": 0.5}
    assert "参考答案" not in chat.calls[0][1]["content"]


def test_run(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SEARCH_MODE", "hybrid")
    monkeypatch.setattr(settings, "EVAL_JUDGE_MODEL", "judge-model")
    FakeRetriever.results = {"q1": [1, 9], "q2": []}
    FakeRetriever.calls = []
    cases = [
        EvalCase("a", "q1", [1], [1, 2], tags=["keyword"], reference_answer="r", key_points=["p1", "p2"]),
        EvalCase("b", "q2", [1], [3], tags=["keyword"]),
    ]
    chat = RoutedChat(
        answers=["回答 a [1]", "回答 a gold [1][2]", "回答 b gold"],
        faithful=[
            {"claims": [{"claim": "x", "verdict": "supported"}, {"claim": "y", "verdict": "unsupported"}]},
            {"claims": [{"claim": "x", "verdict": "supported"}]},
            {"claims": [{"claim": "z", "verdict": "supported"}]},
        ],
        quality=[
            {"relevance": 5, "correctness": 5, "covered": [1], "errors": []},
            {"relevance": 3, "refused": True},  # b 没检索到，直接回复没有找到，不打忠实度
            {"relevance": 5, "correctness": 5, "covered": [1, 2]},
            {"relevance": 5},
        ],
    )
    gold = lambda conn, ids: [_chunk(i) for i in ids]  # noqa: E731
    configs = [gen.GenEvalConfig("hybrid", mode="vector"), gen.GenEvalConfig("gold", context="gold")]
    report = gen.run(None, None, cases, configs, client=chat, retriever_factory=FakeRetriever, gold_loader=gold)

    assert settings.RAG_SEARCH_MODE == "hybrid" and FakeRetriever.calls[0]["top_k"] == settings.QA_TOP_K
    assert set(chat.models) == {"judge-model"}
    assert report["with_reference"] == 1 and report["settings"]["judge_model"] == "judge-model"
    hybrid, gold_cfg = report["configs"]
    a, b = hybrid["details"]
    assert a["metrics"] == {
        "context_recall": 0.5, "citation_valid": 1.0, "faithfulness": 0.5, "hallucination": 1.0,
        "refused": 0.0, "relevance": 1.0, "correctness": 1.0, "completeness": 0.5,
    }
    assert a["context"][1] == {"chunk_id": 9, "doc_id": 1, "relevant": False} and a["missing_points"] == ["p2"]
    assert b["answer"] == NO_RESULT_ANSWER and "faithfulness" not in b["metrics"] and b["metrics"]["refused"] == 1.0
    assert hybrid["summary"]["relevance"] == 0.75 and hybrid["summary"]["faithfulness"] == 0.5
    assert set(hybrid["by_context"]) == {"部分召回", "未召回"}
    assert gold_cfg["summary"]["context_recall"] == 1.0 and gold_cfg["summary"]["faithfulness"] == 1.0
    assert gold_cfg["details"][0]["answer"] == "回答 a gold [1][2]"

    md = gen.to_markdown(report)
    assert "| hybrid | 50.0% | 75.0% | 50.0% | 100.0%" in md
    assert "| hybrid | 未召回 | 1 |" in md
    assert "无依据的陈述：y" in md and "遗漏要点：p2" in md


def test_run_judge_error():
    FakeRetriever.results = {"q": [1]}
    chat = RoutedChat([], [], [])
    chat.complete = lambda messages, model=None, timeout=None: (  # 打分返回的不是 JSON
        "不是 JSON" if messages[0]["content"] == gen.FAITHFULNESS_PROMPT else "回答"
    )
    report = gen.run(None, None, [EvalCase("a", "q", [1], [1])], [gen.GenEvalConfig("h")], client=chat, retriever_factory=FakeRetriever)
    cfg = report["configs"][0]
    assert cfg["judge_errors"] == 1 and cfg["errors"] == 0
    assert cfg["details"][0]["metrics"] == {"context_recall": 1.0} and "judge_error" in cfg["details"][0]


def test_build_reference():
    case = EvalCase("a", "q", [1], [1, 2])
    chat = FakeChat(['{"reference_answer": " 答案 ", "key_points": ["p1", " ", "p2"]}'])
    assert gen.build_reference(None, chat, case, gold_loader=lambda conn, ids: [_chunk(i) for i in ids])
    assert case.reference_answer == "答案" and case.key_points == ["p1", "p2"]
    assert "[2] 《标题》\n片段2" in chat.calls[0][1]["content"]

    chat = FakeChat(['{"reference_answer": "", "key_points": ["p"]}'])  # 资料不能回答
    assert not gen.build_reference(None, chat, case, gold_loader=lambda conn, ids: [_chunk(i) for i in ids])
    assert case.key_points == []
    assert not gen.build_reference(None, chat, case, gold_loader=lambda conn, ids: [])  # 片段已不存在
