"""
RAG 检索评估指标：给定一次检索返回的片段 id（按排名）和标注的相关片段 id，计算召回率、精确度等指标。

    m = evaluate([812, 9, 813], relevant={812, 813}, ks=[1, 3])
    # {"recall@1": 0.5, "precision@1": 1.0, "recall@3": 1.0, "precision@3": 0.667, ...}

核心指标（每个 k 一组）：
- recall@k     召回率：前 k 条覆盖了多少相关片段 = 命中数 / 相关片段总数。漏召回 → 大模型拿不到资料、答不全或编造
- precision@k  精确度：前 k 条中有多少是相关的 = 命中数 / 实际返回条数（不足 k 条时按实际条数，
               RAG_MIN_SCORE 过滤掉低分片段会提升精确度）。精确度低 → 无关片段干扰大模型、浪费上下文
- f1@k         两者的调和平均
- hit@k        前 k 条中至少命中一条相关片段（单个相关片段的评测集上等于 recall@k）
文档级（只要求找到正确的文档，不要求切分块完全一致；切分粒度调整时更稳定）：
- doc_recall@k     前 k 条片段覆盖了多少篇相关文档
- doc_precision@k  前 k 条片段中属于相关文档的比例
排序质量（与 k 无关，取全部返回结果）：
- mrr   第一条相关片段排名的倒数，衡量正确资料是否排在前面
- ndcg@k  考虑全部相关片段位置的排序质量（二元相关性）
"""
import math
from collections.abc import Iterable, Sequence


def _hits(retrieved: Sequence[int], relevant: set[int], k: int) -> int:
    return sum(1 for i in retrieved[:k] if i in relevant)


def recall_at_k(retrieved: Sequence[int], relevant: set[int], k: int) -> float:
    return _hits(retrieved, relevant, k) / len(relevant) if relevant else 0.0


def precision_at_k(retrieved: Sequence[int], relevant: set[int], k: int) -> float:
    returned = min(k, len(retrieved))
    return _hits(retrieved, relevant, k) / returned if returned else 0.0


def f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def reciprocal_rank(retrieved: Sequence[int], relevant: set[int]) -> float:
    return next((1 / rank for rank, i in enumerate(retrieved, 1) if i in relevant), 0.0)


def ndcg_at_k(retrieved: Sequence[int], relevant: set[int], k: int) -> float:
    dcg = sum(1 / math.log2(rank + 1) for rank, i in enumerate(retrieved[:k], 1) if i in relevant)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(k, len(relevant)) + 1))
    return dcg / ideal if ideal else 0.0


def evaluate(
    retrieved: Sequence[int], relevant: set[int], ks: Iterable[int],
    retrieved_docs: Sequence[int] = (), relevant_docs: set[int] = frozenset(),
) -> dict[str, float]:
    """一个问题的全部指标。retrieved_docs 为每条返回片段所属的文档 id（与 retrieved 一一对应），
    relevant_docs 为空时不计算文档级指标。"""
    m: dict[str, float] = {}
    for k in ks:
        p, r = precision_at_k(retrieved, relevant, k), recall_at_k(retrieved, relevant, k)
        m[f"recall@{k}"] = r
        m[f"precision@{k}"] = p
        m[f"f1@{k}"] = f1(p, r)
        m[f"hit@{k}"] = 1.0 if _hits(retrieved, relevant, k) else 0.0
        m[f"ndcg@{k}"] = ndcg_at_k(retrieved, relevant, k)
        if relevant_docs:
            top_docs = retrieved_docs[:k]
            m[f"doc_recall@{k}"] = len(relevant_docs & set(top_docs)) / len(relevant_docs)
            m[f"doc_precision@{k}"] = sum(1 for d in top_docs if d in relevant_docs) / len(top_docs) if top_docs else 0.0
    m["mrr"] = reciprocal_rank(retrieved, relevant)
    return m


def mean(results: list[dict[str, float]]) -> dict[str, float]:
    """多个问题的指标取平均（宏平均：每个问题权重相同）。某个指标只有部分问题有时，按有的问题平均。"""
    keys = dict.fromkeys(k for r in results for k in r)
    return {k: sum(vals) / len(vals) for k in keys if (vals := [r[k] for r in results if k in r])}
