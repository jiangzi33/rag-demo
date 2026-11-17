import pytest

from app.core.config import settings
from app.rag import retrieval_metrics as rm
from app.services import retrieval_eval_service as ev
from app.services.retrieval_service import RetrievedChunk


def test_recall_precision():
    retrieved = [1, 9, 2, 8]
    assert rm.recall_at_k(retrieved, {1, 2, 3}, 1) == pytest.approx(1 / 3)
    assert rm.recall_at_k(retrieved, {1, 2, 3}, 3) == pytest.approx(2 / 3)
    assert rm.precision_at_k(retrieved, {1, 2, 3}, 1) == 1.0
    assert rm.precision_at_k(retrieved, {1, 2, 3}, 4) == 0.5
    # 返回不足 k 条时按实际条数计算精确度；没有返回时为 0
    assert rm.precision_at_k([1], {1}, 5) == 1.0
    assert rm.precision_at_k([], {1}, 5) == 0.0
    assert rm.recall_at_k([1], set(), 5) == 0.0
    assert rm.f1(0.5, 1.0) == pytest.approx(2 / 3)
    assert rm.f1(0, 0) == 0.0


def test_ranking_metrics():
    assert rm.reciprocal_rank([9, 8, 1], {1}) == pytest.approx(1 / 3)
    assert rm.reciprocal_rank([9, 8], {1}) == 0.0
    assert rm.ndcg_at_k([1, 2], {1, 2}, 2) == pytest.approx(1.0)
    assert rm.ndcg_at_k([9, 1], {1}, 2) == pytest.approx(1 / rm.math.log2(3))


def test_evaluate_and_mean():
    m = rm.evaluate([1, 9, 2], {1, 2}, [1, 3], retrieved_docs=[10, 10, 20], relevant_docs={10})
    assert m["recall@1"] == 0.5 and m["precision@1"] == 1.0 and m["hit@1"] == 1.0
    assert m["recall@3"] == 1.0 and m["precision@3"] == pytest.approx(2 / 3)
    assert m["doc_recall@1"] == 1.0 and m["doc_precision@3"] == pytest.approx(2 / 3)
    assert m["mrr"] == 1.0
    assert "doc_recall@1" not in rm.evaluate([1], {1}, [1])  # 没有标注文档时不计算文档级指标
    assert rm.mean([{"a": 1.0, "b": 1.0}, {"a": 0.0}]) == {"a": 0.5, "b": 1.0}


def test_dataset_roundtrip(tmp_path):
    path = tmp_path / "eval.jsonl"
    cases = [ev.EvalCase("q1", "问题", [1], [5], [2], ["keyword"])]
    ev.save_dataset(path, cases)
    path.write_text(path.read_text(encoding="utf-8") + "\n// 注释行\n", encoding="utf-8")
    assert ev.load_dataset(path) == cases
    path.write_text('{"id": "q1", "question": "a", "kb_ids": [1], "relevant_chunk_ids": []}\n' * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        ev.load_dataset(path)


def _chunk(chunk_id, doc_id=1):
    return RetrievedChunk(chunk_id, doc_id, "标题", 1, 0, "", f"片段{chunk_id}", 0.5)


class FakeRetriever:
    """按问题返回预置结果，记录检索时的模式和参数。"""
    results: dict[str, list[int]] = {}
    calls: list[dict] = []

    def __init__(self, min_score=None):
        self.min_score = min_score

    def retrieve(self, conn, user, question, kb_ids, top_k=None, keyword_text=None):
        FakeRetriever.calls.append({"mode": settings.RAG_SEARCH_MODE, "question": question, "top_k": top_k, "keyword_text": keyword_text})
        return [_chunk(i) for i in self.results.get(question, [])][:top_k]


class FakeChat:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def complete(self, messages, model=None, timeout=None):
        self.calls.append(messages)
        return self.replies.pop(0)


def test_run(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SEARCH_MODE", "hybrid")
    FakeRetriever.results = {"q1": [1, 9, 2], "Q1 改写": [1, 2], "q2": []}
    FakeRetriever.calls = []
    cases = [
        ev.EvalCase("a", "q1", [1], [1, 2], [1], ["keyword"]),
        ev.EvalCase("b", "q2", [1], [3], [1], ["paraphrase"]),
    ]
    chat = FakeChat(['{"query": "Q1 改写", "keywords": ["k"]}', '{"query": "q2"}'])
    configs = [ev.EvalConfig("vector", "vector"), ev.EvalConfig("hybrid+rewrite", "hybrid", rewrite=True)]
    report = ev.run(None, None, cases, configs, [1, 3, 50], client=chat, retriever_factory=FakeRetriever)

    assert report["ks"] == [1, 3]  # 超过 RAG_TOP_K_MAX 的 k 去掉
    assert settings.RAG_SEARCH_MODE == "hybrid"  # 评估后恢复原配置
    assert [c["mode"] for c in FakeRetriever.calls] == ["vector", "vector", "hybrid", "hybrid"]
    assert FakeRetriever.calls[2]["keyword_text"] == "q1 Q1 改写 k"
    vector, rewrite = report["configs"]
    assert vector["summary"]["recall@3"] == 0.5  # a 全部召回，b 没召回
    assert vector["summary"]["precision@3"] == pytest.approx(1 / 3)  # a 2/3，b 没有返回为 0
    assert rewrite["summary"]["precision@3"] == 0.5
    assert vector["by_tag"]["keyword"]["recall@3"] == 1.0 and vector["by_tag"]["paraphrase"]["cases"] == 1
    assert vector["details"][0]["retrieved"][1] == {"chunk_id": 9, "doc_id": 1, "relevant": False, "score": 0.5, "keyword_score": None}

    md = ev.to_markdown(report, main_k=3)
    assert "| vector | 50.0% | 33.3%" in md
    assert "`b` q2" in md  # 列出完全没召回的问题

    with pytest.raises(ValueError, match="大模型"):
        ev.run(None, None, cases, configs, [1], client=None, retriever_factory=FakeRetriever)


def test_judge_case():
    FakeRetriever.results = {"q": [1, 7, 8]}
    case = ev.EvalCase("a", "q", [1], [1], [1])
    chat = FakeChat(['```json\n{"relevant": [2, 2, 5, "x"]}\n```'])
    added = ev.judge_case(None, None, chat, case, pool_k=3, retriever_factory=FakeRetriever)
    assert added == 1
    assert case.relevant_chunk_ids == [1, 8] and case.relevant_doc_ids == [1] and case.judged
    prompt = chat.calls[0][1]["content"]
    assert "[1] 《标题》\n片段7" in prompt and "片段1" not in prompt  # 只判定尚未标注的候选
