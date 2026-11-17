"""
RAG 检索评估：用标注好的评测集（问题 → 相关片段）离线评估检索效果，对比不同检索配置的召回率和精确度。

    cases = load_dataset("eval/retrieval_eval.jsonl")
    report = run(conn, user, cases, [EvalConfig("hybrid+rewrite", "hybrid", rewrite=True)], ks=[1, 3, 5, 10], client=client)

评测集（JSONL，一行一个问题）：
    {"id": "q001", "question": "InnoDB 怎么实现事务？", "kb_ids": [1], "relevant_chunk_ids": [812, 813],
     "relevant_doc_ids": [12], "tags": ["paraphrase"], "history": [], "source": "generated", "judged": true}
- relevant_chunk_ids：能回答问题的片段（document_chunks.id）；文档编辑 / --rechunk 后片段 id 会变，需要重新标注
- tags：问题类型，报告中按类型分别统计（如 keyword 类问题考察关键词检索，paraphrase 类考察语义检索）
- history：追问的对话历史，与问答接口相同，用于评估 Query Rewrite

构建评测集（见 scripts/build_eval_set.py）：
1. generate：从知识库随机抽取片段，让大模型针对片段出题，该片段即标准答案（只有 1 个相关片段）
2. judge：对每个问题，汇总各检索配置返回的候选片段（pooling），让大模型逐条判定是否相关，补全相关片段。
   只有 1 个标注片段时，其他同样能回答问题的片段会被算作错误，精确度会被严重低估；判定后召回率和精确度才都可信
3. 人工抽查修正：大模型判定会有误差，正式对比前抽查一部分

评估（run）：每个配置对每个问题检索一次（取最大的 k），截取前 k 条计算各 k 的指标，再取平均；
同时按 tags 分组统计、记录每个问题的检索结果，便于定位漏召回和误召回。
"""
import json
import logging
import random
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlalchemy import Connection

from app.core.config import settings
from app.mapper import document_chunk_mapper, document_mapper, user_mapper
from app.models.document_chunk import DocumentChunk
from app.models.user import User
from app.rag import retrieval_metrics
from app.rag.llm import ChatError, OpenAIChatClient
from app.services import query_rewrite_service
from app.services.qa_service import retrieval_query
from app.services.query_rewrite_service import RewrittenQuery
from app.services.retrieval_service import RetrievedChunk, Retriever

logger = logging.getLogger(__name__)

_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


@dataclass
class EvalCase:
    id: str
    question: str
    kb_ids: list[int]
    relevant_chunk_ids: list[int]
    relevant_doc_ids: list[int] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    source: str = "manual"  # manual：人工标注；generated：大模型针对片段出题
    judged: bool = False  # 是否已用大模型判定补全相关片段


@dataclass
class EvalConfig:
    name: str
    mode: str  # hybrid / vector，同 RAG_SEARCH_MODE
    rewrite: bool = False  # 是否先用大模型改写问题（同问答接口的 QA_REWRITE_ENABLED）
    min_score: float | None = None  # 不填使用 RAG_MIN_SCORE


# ==================== 评测集读写 ====================

def load_dataset(path: str | Path) -> list[EvalCase]:
    cases = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        try:
            cases.append(EvalCase(**json.loads(line)))
        except (ValueError, TypeError) as e:
            raise ValueError(f"{path} 第 {n} 行格式错误：{e}") from e
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path} 中有重复的 id")
    return cases


def save_dataset(path: str | Path, cases: list[EvalCase]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(asdict(c), ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")


def stale_chunk_ids(conn: Connection, cases: list[EvalCase]) -> set[int]:
    """标注的相关片段中已不存在的 id（文档被编辑、删除或重新切分），这些问题需要重新标注。"""
    ids = list({i for c in cases for i in c.relevant_chunk_ids})
    return set(ids) - {c.id for c in document_chunk_mapper.select_by_ids(conn, ids)}


def eval_user(conn: Connection, username: str | None = None) -> User:
    """以哪个用户的身份检索：默认取第一个存在的系统管理员（能读全部知识库，评估不受权限范围影响）。"""
    for name in [username] if username else sorted(settings.admin_usernames):
        if user := user_mapper.select_by_username(conn, name):
            return user
    raise ValueError(f"用户不存在：{username or settings.ADMIN_USERNAMES}")


# ==================== 评估 ====================

@contextmanager
def _search_mode(mode: str) -> Iterator[None]:
    old = settings.RAG_SEARCH_MODE, settings.RAG_LOG_RESULTS
    settings.RAG_SEARCH_MODE, settings.RAG_LOG_RESULTS = mode, False  # 评测时每次检索都打日志太多
    try:
        yield
    finally:
        settings.RAG_SEARCH_MODE, settings.RAG_LOG_RESULTS = old


def search(
    conn: Connection, user: User, retriever: Retriever, case: EvalCase, top_k: int, rewritten: RewrittenQuery | None,
) -> tuple[str, list[RetrievedChunk]]:
    """与问答接口（qa_service.retrieve）相同的检索方式，返回实际检索用的问题和结果。"""
    if rewritten:
        return rewritten.query, retriever.retrieve(
            conn, user, rewritten.query, case.kb_ids, top_k, keyword_text=rewritten.keyword_text(case.question)
        )
    query = retrieval_query(case.question, case.history)
    return query, retriever.retrieve(conn, user, query, case.kb_ids, top_k)


def run(
    conn: Connection, user: User, cases: list[EvalCase], configs: list[EvalConfig], ks: list[int],
    client: OpenAIChatClient | None = None, retriever_factory=Retriever,
) -> dict:
    """依次评估每个配置，返回报告（可直接 json.dumps）。需要改写的配置必须传入 client；
    同一个问题只改写一次，各配置共用改写结果，避免改写的随机性影响对比。"""
    ks = sorted(set(ks))
    top_k = min(max(ks), settings.RAG_TOP_K_MAX)
    ks = [k for k in ks if k <= top_k]
    rewrites: dict[str, RewrittenQuery | None] = {}
    report = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cases": len(cases),
        "ks": ks,
        "settings": {
            "embedding_model": settings.OLLAMA_EMBEDDING_MODEL,
            "chunk_size": settings.RAG_CHUNK_SIZE,
            "chunk_overlap": settings.RAG_CHUNK_OVERLAP,
            "hybrid_candidates": settings.RAG_HYBRID_CANDIDATES,
            "rrf_k": settings.RAG_RRF_K,
            "keyword_weight": settings.RAG_KEYWORD_WEIGHT,
            "rewrite_model": settings.QA_REWRITE_MODEL or settings.OPENAI_MODEL,
        },
        "configs": [],
    }
    for cfg in configs:
        if cfg.rewrite and client is None:
            raise ValueError(f"配置 {cfg.name} 需要 Query Rewrite，但没有可用的大模型客户端")
        retriever = retriever_factory(min_score=cfg.min_score)
        details, latencies, errors = [], [], 0
        with _search_mode(cfg.mode):
            for case in cases:
                if cfg.rewrite and case.id not in rewrites:
                    rewrites[case.id] = query_rewrite_service.rewrite(client, case.question, case.history)
                started = time.perf_counter()
                try:
                    query, chunks = search(conn, user, retriever, case, top_k, rewrites.get(case.id) if cfg.rewrite else None)
                except Exception as e:
                    errors += 1
                    logger.warning("配置 %s 问题 %s 检索失败：%s", cfg.name, case.id, e)
                    query, chunks = case.question, []
                latencies.append(time.perf_counter() - started)
                relevant, relevant_docs = set(case.relevant_chunk_ids), set(case.relevant_doc_ids)
                metrics = retrieval_metrics.evaluate(
                    [c.chunk_id for c in chunks], relevant, ks, [c.doc_id for c in chunks], relevant_docs
                )
                details.append({
                    "id": case.id,
                    "question": case.question,
                    "query": query,
                    "tags": case.tags,
                    "relevant_chunk_ids": case.relevant_chunk_ids,
                    "retrieved": [
                        {"chunk_id": c.chunk_id, "doc_id": c.doc_id, "relevant": c.chunk_id in relevant,
                         "score": round(c.score, 4), "keyword_score": c.keyword_score}
                        for c in chunks
                    ],
                    "metrics": metrics,
                })
        tags = dict.fromkeys(t for case in cases for t in case.tags)
        report["configs"].append({
            **asdict(cfg),
            "summary": retrieval_metrics.mean([d["metrics"] for d in details]),
            "by_tag": {
                t: {"cases": len(group), **retrieval_metrics.mean(group)}
                for t in tags if (group := [d["metrics"] for d in details if t in d["tags"]])
            },
            "avg_latency_ms": round(sum(latencies) / len(latencies) * 1000, 1) if latencies else 0,
            "errors": errors,
            "details": details,
        })
    return report


def to_markdown(report: dict, main_k: int) -> str:
    """报告摘要：各配置在每个 k 下的召回率 / 精确度对比，主 k 下按问题类型的分组，以及主 k 下完全没召回的问题。"""
    configs = report["configs"]
    lines = [
        f"# RAG 检索评估报告（{report['created_at']}）",
        "",
        f"问题数 {report['cases']}；配置：" + "，".join(f"`{k}={v}`" for k, v in report["settings"].items()),
        "",
    ]
    for k in report["ks"]:
        lines += [
            f"## Top {k}",
            "",
            "| 配置 | Recall | Precision | F1 | Hit | nDCG | 文档 Recall | 文档 Precision |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for c in configs:
            s = c["summary"]
            cols = ["recall", "precision", "f1", "hit", "ndcg", "doc_recall", "doc_precision"]
            lines.append(f"| {c['name']} | " + " | ".join(_pct(s.get(f"{m}@{k}")) for m in cols) + " |")
        lines.append("")
    lines += ["## 排序与耗时", "", "| 配置 | MRR | 平均耗时 | 检索失败 |", "|---|---|---|---|"]
    lines += [f"| {c['name']} | {c['summary'].get('mrr', 0):.3f} | {c['avg_latency_ms']} ms | {c['errors']} |" for c in configs]
    lines.append("")

    tags = list(dict.fromkeys(t for c in configs for t in c["by_tag"]))
    if tags:
        lines += [f"## 按问题类型（Top {main_k}，Recall / Precision）", "", "| 类型 | 问题数 | " + " | ".join(c["name"] for c in configs) + " |"]
        lines.append("|---|---|" + "---|" * len(configs))
        for t in tags:
            n = next(c["by_tag"][t]["cases"] for c in configs if t in c["by_tag"])
            cells = [
                f"{_pct(g.get(f'recall@{main_k}'))} / {_pct(g.get(f'precision@{main_k}'))}" if (g := c["by_tag"].get(t)) else "-"
                for c in configs
            ]
            lines.append(f"| {t} | {n} | " + " | ".join(cells) + " |")
        lines.append("")

    for c in configs:
        missed = [d for d in c["details"] if d["metrics"].get(f"recall@{main_k}", 0) == 0]
        if not missed:
            continue
        lines += [f"## {c['name']}：Top {main_k} 完全没召回的问题（{len(missed)}）", ""]
        for d in missed:
            got = ", ".join(str(r["chunk_id"]) for r in d["retrieved"][:main_k]) or "无"
            query = f"（检索：{d['query']}）" if d["query"] != d["question"] else ""
            lines.append(f"- `{d['id']}` {d['question']}{query} —— 应召回 {d['relevant_chunk_ids']}，实际 [{got}]")
        lines.append("")
    return "\n".join(lines)


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


# ==================== 构建评测集 ====================

STYLES = {
    "factual": "直接询问片段中的一个具体事实、步骤或结论，可以使用片段中的原词。",
    "paraphrase": "用与片段不同的说法提问：换用同义词、描述现象或需求，尽量不要照抄片段中的关键词。",
    "keyword": "围绕片段中的专有名词、术语、命令、配置项、型号或错误码提问，问题中必须原样包含这个词。",
    "colloquial": "像普通用户一样口语化、简短地提问，可以模糊一些，但必须仍然能由这个片段回答。",
}

GENERATE_PROMPT = """你在为企业知识库的检索系统构建评测集。根据给出的文档片段，出一个用户可能会问的问题，要求：
1. 问题的答案必须能在这个片段中找到；问题要独立完整，不要出现“这段文字”“上文”“该片段”等指代。
2. 提问风格：{style}
3. 片段内容过少、只是目录或没有实质信息时，question 返回空字符串。
只输出 JSON，不要输出其他内容：{{"question": "问题"}}"""

JUDGE_PROMPT = """你在为知识库检索系统标注评测数据。判断每个候选片段是否与问题相关。
相关：片段包含能回答问题的信息（完整答案，或答案中必要的一部分）。
不相关：只是话题相近、提到了相同的词，但不能帮助回答这个问题。
只输出 JSON，不要输出其他内容：{"relevant": [相关片段的编号]}"""


def _parse_json(reply: str) -> dict:
    match = _JSON_OBJECT.search(reply or "")
    if not match:
        raise ValueError(f"返回内容不是 JSON：{(reply or '')[:200]}")
    data = json.loads(match.group())
    if not isinstance(data, dict):
        raise ValueError(f"返回内容不是 JSON 对象：{match.group()[:200]}")
    return data


def _chunk_text(chunk: DocumentChunk, title: str) -> str:
    heading = " > ".join(chunk.heading_path)
    return f"《{title}》{(' > ' + heading) if heading else ''}\n{chunk.content}"


def generate_cases(
    conn: Connection, client: OpenAIChatClient, n: int, kb_ids: list[int] | None = None,
    min_chars: int = 80, seed: int | None = None, id_prefix: str = "g",
) -> list[EvalCase]:
    """随机抽取 n 个片段（正文不少于 min_chars 字），每个片段让大模型按一种风格出一道题，风格轮流使用。
    出题的片段即相关片段；大模型认为片段不适合出题时跳过（返回的问题可能少于 n 个）。"""
    rng = random.Random(seed)
    chunks = [c for c in document_chunk_mapper.select_all(conn, kb_ids) if len(c.content.strip()) >= min_chars]
    sample = rng.sample(chunks, min(n, len(chunks)))
    titles = {d["id"]: d["title"] for d in document_mapper.select_brief_by_ids(conn, list({c.doc_id for c in sample}))}
    styles = list(STYLES)
    cases = []
    for i, chunk in enumerate(sample):
        style = styles[i % len(styles)]
        messages = [
            {"role": "system", "content": GENERATE_PROMPT.format(style=STYLES[style])},
            {"role": "user", "content": f"文档片段：\n{_chunk_text(chunk, titles.get(chunk.doc_id, ''))}"},
        ]
        try:
            question = str(_parse_json(client.complete(messages)).get("question") or "").strip()
        except (ChatError, ValueError) as e:
            logger.warning("片段 %s 出题失败：%s", chunk.id, e)
            continue
        if not question:
            continue
        cases.append(EvalCase(
            id=f"{id_prefix}{chunk.id}", question=" ".join(question.split()), kb_ids=[chunk.kb_id],
            relevant_chunk_ids=[chunk.id], relevant_doc_ids=[chunk.doc_id], tags=[style], source="generated",
        ))
    return cases


def judge_case(
    conn: Connection, user: User, client: OpenAIChatClient, case: EvalCase, pool_k: int = 10,
    modes: tuple[str, ...] = ("vector", "hybrid"), retriever_factory=Retriever,
) -> int:
    """pooling 补全相关片段：汇总各检索方式前 pool_k 条候选中尚未标注的片段，让大模型判定是否相关，
    相关的加入 relevant_chunk_ids / relevant_doc_ids。返回新增的相关片段数。
    没进入任何候选池的相关片段仍然标注不到，所以 pool_k 应不小于评估时最大的 k。"""
    candidates: dict[int, RetrievedChunk] = {}
    for mode in modes:
        with _search_mode(mode):
            _, chunks = search(conn, user, retriever_factory(min_score=None), case, min(pool_k, settings.RAG_TOP_K_MAX), None)
        for c in chunks:
            candidates.setdefault(c.chunk_id, c)
    labeled = set(case.relevant_chunk_ids)
    pending = [c for i, c in candidates.items() if i not in labeled]
    if pending:
        refs = "\n\n".join(
            f"[{i}] 《{c.doc_title}》{(' > ' + c.heading) if c.heading else ''}\n{c.content}" for i, c in enumerate(pending, 1)
        )
        reply = client.complete([
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": f"问题：{case.question}\n\n候选片段：\n\n{refs}"},
        ])
        picked = _parse_json(reply).get("relevant") or []
        found = [pending[i - 1] for i in dict.fromkeys(picked) if isinstance(i, int) and 1 <= i <= len(pending)]
    else:
        found = []
    case.relevant_chunk_ids += [c.chunk_id for c in found]
    case.relevant_doc_ids += [d for d in dict.fromkeys(c.doc_id for c in found) if d not in case.relevant_doc_ids]
    case.judged = True
    return len(found)
