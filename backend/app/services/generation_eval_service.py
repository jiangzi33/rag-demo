"""
RAG 生成评估：检索到片段之后，评估大模型是否基于这些片段正确地生成了回答（LLM-as-judge，离线评估）。

    cases = retrieval_eval_service.load_dataset("eval/retrieval_eval.jsonl")   # 与检索评估共用评测集
    report = run(conn, user, cases, [GenEvalConfig("hybrid+rewrite", rewrite=True)], client=client, judge=judge)

四个维度（每个问题 0~1，报告中取平均）：
- faithfulness  忠实度：回答拆成事实陈述，逐条对照“交给大模型的片段”判定，有依据的比例。
                只看回答是否来自片段，不看对错：片段外的知识即使正确也算无依据（幻觉）。
                hallucination 为含无依据 / 矛盾陈述的回答比例；citation_valid 为引用编号 [n] 有效的比例
- relevance     相关性：回答是否直接回应了问题，没有答非所问、大段无关内容（1~5 分归一化）
- completeness  完整性：参考要点（key_points）被回答覆盖的比例
- correctness   正确性：回答与参考答案（reference_answer）的事实是否一致，有矛盾的错误时得分很低（1~5 分归一化）
忠实度、相关性只需要问题和片段；完整性、正确性需要评测集中有参考答案和要点（scripts/build_eval_set.py reference 生成）。

上下文来源（GenEvalConfig.context）：
- retrieved：与问答接口相同，先检索再生成，评估端到端效果
- gold：直接把标注的相关片段交给大模型，排除检索的影响，只评估生成能力
retrieved 模式下同时记录 context_recall（相关片段有多少进入了上下文），报告按“召回完整 / 部分召回 / 未召回”分组，
区分回答不好是检索漏了资料，还是资料齐全但大模型没用好。

裁判模型（EVAL_JUDGE_MODEL）建议用比生成模型更强的模型；大模型打分有随机性，对比配置时用同一个裁判、同一批问题。
"""
import logging
import re
import time
from dataclasses import asdict, dataclass

from sqlalchemy import Connection

from app.core.config import settings
from app.mapper import document_chunk_mapper, document_mapper
from app.models.user import User
from app.rag import retrieval_metrics
from app.rag.llm import ChatError, OpenAIChatClient
from app.services import query_rewrite_service
from app.services import retrieval_eval_service as ev
from app.services.qa_service import NO_RESULT_ANSWER, build_messages
from app.services.query_rewrite_service import RewrittenQuery
from app.services.retrieval_eval_service import EvalCase
from app.services.retrieval_service import RetrievedChunk, Retriever

logger = logging.getLogger(__name__)

_CITATION = re.compile(r"\[(\d+)\]")
METRICS = ["faithfulness", "hallucination", "citation_valid", "relevance", "completeness", "correctness", "refused"]


@dataclass
class GenEvalConfig:
    name: str
    context: str = "retrieved"  # retrieved：先检索再生成；gold：直接使用标注的相关片段
    mode: str = "hybrid"  # 检索方式，同 RAG_SEARCH_MODE（context=gold 时不用）
    rewrite: bool = False  # 检索前是否改写问题
    top_k: int | None = None  # 交给大模型的片段数，不填使用 QA_TOP_K
    model: str | None = None  # 生成回答的模型，不填使用 OPENAI_MODEL


# ==================== 提示词 ====================

FAITHFULNESS_PROMPT = """你在评估知识库问答系统的回答是否忠实于参考资料。
1. 把“回答”拆成若干条独立的事实陈述（claim），忽略寒暄、格式、过渡语，以及“资料中没有找到……”这类说明。
2. 逐条对照“参考资料”判定 verdict：
   supported：参考资料中明确写有，或能直接推出；
   unsupported：参考资料中没有提到（来自模型自身知识或编造），即使它本身是正确的；
   contradicted：与参考资料矛盾。
3. sources 填支持该陈述的资料编号，unsupported 时为空列表。
只输出 JSON，不要输出其他内容：{"claims": [{"claim": "陈述", "verdict": "supported", "sources": [1]}]}"""

QUALITY_PROMPT = """你在评估知识库问答系统的回答质量。给出用户问题、待评估回答，以及（可能有的）参考答案和要点；
参考答案和要点由标注的相关资料整理而来，视为标准答案。按以下标准评估：
- relevance（相关性，1~5）：回答是否针对问题本身。5 直接、准确地回应问题；3 回应了问题但有较多无关内容或偏题；
  1 答非所问。只看是否切题，不看对错；如实说明“没有找到相关信息”算切题（3 分）。
- correctness（正确性，1~5）：回答中的事实是否与参考答案一致。5 完全一致；3 主体正确但有小的不准确；
  有与参考答案矛盾的错误时不超过 2 分；参考答案有答案而回答说没有找到时为 1 分。
  参考答案之外、不矛盾的补充不扣分，缺少内容由要点衡量，这里不扣分。没有参考答案时填 null。
- covered：回答覆盖了哪些要点（编号），意思表达到即可，不要求原词；没有要点时为空列表。
- errors：回答中与参考答案矛盾或错误的地方，没有为空列表。
- refused：回答是否表示知识库中没有找到相关信息、无法回答。
只输出 JSON，不要输出其他内容：
{"relevance": 5, "correctness": 5, "covered": [1, 2], "errors": [], "refused": false, "reason": "一句话说明"}"""

REFERENCE_PROMPT = """你在为知识库问答系统构建评测集的标准答案。根据给出的问题和相关资料：
1. reference_answer：只根据资料写出准确、完整、简洁的答案，不要加入资料以外的信息。
2. key_points：一个好的回答必须包含的 1~6 个关键要点，每条是一个独立、简短、明确的事实。
3. 资料不能回答这个问题时，reference_answer 返回空字符串，key_points 返回空列表。
只输出 JSON，不要输出其他内容：{"reference_answer": "答案", "key_points": ["要点"]}"""


def _refs(sources: list[RetrievedChunk]) -> str:
    """与问答接口（qa_service.build_messages）相同的参考资料格式，编号对应回答中的 [n]。"""
    return "\n\n".join(
        f"[{i}] 《{c.doc_title}》{(' > ' + c.heading) if c.heading else ''}\n{c.content}" for i, c in enumerate(sources, 1)
    )


def _score(value) -> float | None:
    """1~5 分归一化到 0~1，不是数字时返回 None（不计入平均）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return (min(max(float(value), 1), 5) - 1) / 4


# ==================== 片段 ====================

def load_gold_chunks(conn: Connection, chunk_ids: list[int]) -> list[RetrievedChunk]:
    """标注的相关片段，按标注顺序转成与检索结果相同的结构（已不存在的片段跳过）。"""
    chunks = {c.id: c for c in document_chunk_mapper.select_by_ids(conn, chunk_ids)}
    titles = {d["id"]: d["title"] for d in document_mapper.select_brief_by_ids(conn, list({c.doc_id for c in chunks.values()}))}
    return [
        RetrievedChunk(c.id, c.doc_id, titles.get(c.doc_id, ""), c.kb_id, c.chunk_index, " > ".join(c.heading_path), c.content, 1.0)
        for i in chunk_ids if (c := chunks.get(i))
    ]


# ==================== 打分 ====================

def judge_faithfulness(judge: OpenAIChatClient, case: EvalCase, answer: str, sources: list[RetrievedChunk]) -> dict:
    """返回 {"metrics": {...}, "claims": [...]}。回答中没有事实陈述（如只说没有找到）时不计忠实度。"""
    cited = [int(n) for n in _CITATION.findall(answer)]
    metrics = {"citation_valid": sum(1 <= n <= len(sources) for n in cited) / len(cited)} if cited else {}
    reply = judge.complete([
        {"role": "system", "content": FAITHFULNESS_PROMPT},
        {"role": "user", "content": f"参考资料：\n\n{_refs(sources)}\n\n---\n问题：{case.question}\n\n回答：\n{answer}"},
    ], model=settings.EVAL_JUDGE_MODEL)
    claims = [
        {"claim": str(c.get("claim", "")), "verdict": str(c.get("verdict", "")).lower(), "sources": c.get("sources") or []}
        for c in ev._parse_json(reply).get("claims") or [] if isinstance(c, dict)
    ]
    if claims:
        supported = sum(c["verdict"] == "supported" for c in claims)
        metrics["faithfulness"] = supported / len(claims)
        metrics["hallucination"] = float(supported < len(claims))
    return {"metrics": metrics, "claims": claims}


def judge_quality(judge: OpenAIChatClient, case: EvalCase, answer: str) -> dict:
    """相关性、正确性、完整性。没有参考答案 / 要点时不计正确性 / 完整性。"""
    parts = [f"问题：{case.question}"]
    if case.reference_answer:
        parts.append(f"参考答案：\n{case.reference_answer}")
    if case.key_points:
        parts.append("要点：\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(case.key_points, 1)))
    parts.append(f"待评估回答：\n{answer}")
    data = ev._parse_json(judge.complete([
        {"role": "system", "content": QUALITY_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ], model=settings.EVAL_JUDGE_MODEL))

    metrics = {"refused": float(bool(data.get("refused")))}
    if (relevance := _score(data.get("relevance"))) is not None:
        metrics["relevance"] = relevance
    if case.reference_answer and (correctness := _score(data.get("correctness"))) is not None:
        metrics["correctness"] = correctness
    covered = sorted({i for i in data.get("covered") or [] if isinstance(i, int) and 1 <= i <= len(case.key_points)})
    if case.key_points:
        metrics["completeness"] = len(covered) / len(case.key_points)
    return {
        "metrics": metrics,
        "missing_points": [p for i, p in enumerate(case.key_points, 1) if i not in covered],
        "errors": [str(e) for e in data.get("errors") or []],
        "reason": str(data.get("reason") or ""),
    }


# ==================== 评估 ====================

def _context_group(recall: float) -> str:
    return "召回完整" if recall >= 1 else "未召回" if recall <= 0 else "部分召回"


def run(
    conn: Connection, user: User, cases: list[EvalCase], configs: list[GenEvalConfig],
    client: OpenAIChatClient, judge: OpenAIChatClient | None = None,
    retriever_factory=Retriever, gold_loader=load_gold_chunks,
) -> dict:
    """依次评估每个配置：取上下文 → 用问答接口相同的提示词生成回答（非流式）→ 裁判打分。返回报告（可直接 json.dumps）。
    judge 不传时用 client 打分（模型为 EVAL_JUDGE_MODEL）；同一个问题只改写一次，各配置共用。"""
    judge = judge or client
    rewrites: dict[str, RewrittenQuery | None] = {}
    report = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cases": len(cases),
        "with_reference": sum(1 for c in cases if c.reference_answer),
        "settings": {
            "generate_model": client.model,
            "judge_model": settings.EVAL_JUDGE_MODEL or judge.model,
            "qa_top_k": settings.QA_TOP_K,
            "embedding_model": settings.OLLAMA_EMBEDDING_MODEL,
            "chunk_size": settings.RAG_CHUNK_SIZE,
            "rewrite_model": settings.QA_REWRITE_MODEL or settings.OPENAI_MODEL,
        },
        "configs": [],
    }
    for cfg in configs:
        top_k = min(cfg.top_k or settings.QA_TOP_K, settings.RAG_TOP_K_MAX)
        retriever = retriever_factory(min_score=None) if cfg.context == "retrieved" else None
        details, errors, judge_errors = [], 0, 0
        for n, case in enumerate(cases, 1):
            started = time.perf_counter()
            query = case.question
            try:
                if cfg.context == "gold":
                    sources = gold_loader(conn, case.relevant_chunk_ids)
                else:
                    if cfg.rewrite and case.id not in rewrites:
                        rewrites[case.id] = query_rewrite_service.rewrite(client, case.question, case.history)
                    with ev._search_mode(cfg.mode):
                        query, sources = ev.search(
                            conn, user, retriever, case, top_k, rewrites.get(case.id) if cfg.rewrite else None
                        )
                # 与问答接口一致：一个片段都没有时不调用大模型
                answer = client.complete(build_messages(case.question, case.history, sources), model=cfg.model) if sources else NO_RESULT_ANSWER
            except Exception as e:
                errors += 1
                logger.warning("配置 %s 问题 %s 生成失败：%s", cfg.name, case.id, e)
                continue
            latency = time.perf_counter() - started

            relevant = set(case.relevant_chunk_ids)
            metrics = {"context_recall": retrieval_metrics.recall_at_k([c.chunk_id for c in sources], relevant, len(sources))}
            detail = {
                "id": case.id,
                "question": case.question,
                "query": query,
                "tags": case.tags,
                "context": [{"chunk_id": c.chunk_id, "doc_id": c.doc_id, "relevant": c.chunk_id in relevant} for c in sources],
                "answer": answer,
                "reference_answer": case.reference_answer,
                "latency_ms": round(latency * 1000, 1),
            }
            try:
                if sources:
                    faithful = judge_faithfulness(judge, case, answer, sources)
                    metrics |= faithful["metrics"]
                    detail["claims"] = faithful["claims"]
                quality = judge_quality(judge, case, answer)
                metrics |= quality.pop("metrics")
                detail |= quality
            except (ChatError, ValueError) as e:
                judge_errors += 1
                logger.warning("配置 %s 问题 %s 打分失败：%s", cfg.name, case.id, e)
                detail["judge_error"] = str(e)
            detail["metrics"] = metrics
            details.append(detail)
            logger.info("[%s %d/%d] %s %s", cfg.name, n, len(cases), case.id, metrics)

        tags = dict.fromkeys(t for case in cases for t in case.tags)
        groups = dict.fromkeys(["召回完整", "部分召回", "未召回"])
        report["configs"].append({
            **asdict(cfg),
            "summary": retrieval_metrics.mean([d["metrics"] for d in details]),
            "by_tag": {
                t: {"cases": len(g), **retrieval_metrics.mean(g)}
                for t in tags if (g := [d["metrics"] for d in details if t in d["tags"]])
            },
            "by_context": {
                name: {"cases": len(g), **retrieval_metrics.mean(g)}
                for name in groups
                if (g := [d["metrics"] for d in details if _context_group(d["metrics"]["context_recall"]) == name])
            },
            "avg_latency_ms": round(sum(d["latency_ms"] for d in details) / len(details), 1) if details else 0,
            "errors": errors,
            "judge_errors": judge_errors,
            "details": details,
        })
    return report


# ==================== 报告 ====================

_COLUMNS = [
    ("faithfulness", "忠实度"), ("relevance", "相关性"), ("completeness", "完整性"), ("correctness", "正确性"),
    ("hallucination", "幻觉率"), ("citation_valid", "引用有效"), ("refused", "拒答率"), ("context_recall", "上下文召回"),
]


def _row(name: str, m: dict, extra: str = "") -> str:
    return f"| {name} |{extra} " + " | ".join(ev._pct(m.get(k)) for k, _ in _COLUMNS) + " |"


def _header(first: str, extra: str = "") -> list[str]:
    return [
        f"| {first} |{extra} " + " | ".join(label for _, label in _COLUMNS) + " |",
        "|---|" + "---|" * (extra.count("|") + len(_COLUMNS)),
    ]


def to_markdown(report: dict, max_bad_cases: int = 20) -> str:
    """报告摘要：各配置四个维度的对比，按上下文召回情况 / 问题类型分组，以及每个配置的 bad case。"""
    configs = report["configs"]
    lines = [
        f"# RAG 生成评估报告（{report['created_at']}）",
        "",
        f"问题数 {report['cases']}（有参考答案 {report['with_reference']}）；配置："
        + "，".join(f"`{k}={v}`" for k, v in report["settings"].items()),
        "",
        "## 总览",
        "",
        *_header("配置"),
        *(_row(c["name"], c["summary"]) for c in configs),
        "",
        "| 配置 | 上下文 | 平均耗时 | 生成失败 | 打分失败 |",
        "|---|---|---|---|---|",
        *(f"| {c['name']} | {c['context']} | {c['avg_latency_ms']} ms | {c['errors']} | {c['judge_errors']} |" for c in configs),
        "",
    ]
    if any(c["context"] == "retrieved" for c in configs):
        lines += ["## 按上下文召回情况", "", "召回完整但得分低 → 生成问题（提示词 / 模型）；未召回时得分低 → 检索问题。", ""]
        lines += _header("配置", " 召回情况 | 问题数 |")
        for c in configs:
            if c["context"] == "retrieved":
                lines += [_row(c["name"], g, f" {name} | {g['cases']} |") for name, g in c["by_context"].items()]
        lines.append("")
    tags = list(dict.fromkeys(t for c in configs for t in c["by_tag"]))
    if tags:
        lines += ["## 按问题类型", ""]
        lines += _header("配置", " 类型 | 问题数 |")
        for c in configs:
            lines += [_row(c["name"], g, f" {t} | {g['cases']} |") for t, g in c["by_tag"].items()]
        lines.append("")

    for c in configs:
        bad = [d for d in c["details"] if _is_bad(d["metrics"])]
        if not bad:
            continue
        lines += [f"## {c['name']}：问题回答（{len(bad)}）", ""]
        for d in bad[:max_bad_cases]:
            m = d["metrics"]
            scores = "，".join(f"{label} {ev._pct(m[k])}" for k, label in _COLUMNS[:4] if k in m)
            lines.append(f"- `{d['id']}` {d['question']} —— {scores}")
            unsupported = [x["claim"] for x in d.get("claims", []) if x["verdict"] != "supported"]
            if unsupported:
                lines.append(f"  - 无依据的陈述：{'；'.join(unsupported[:3])}")
            if d.get("errors"):
                lines.append(f"  - 错误：{'；'.join(d['errors'][:3])}")
            if d.get("missing_points"):
                lines.append(f"  - 遗漏要点：{'；'.join(d['missing_points'][:3])}")
        if len(bad) > max_bad_cases:
            lines.append(f"- …… 其余 {len(bad) - max_bad_cases} 个见 JSON 报告")
        lines.append("")
    return "\n".join(lines)


def _is_bad(m: dict) -> bool:
    return m.get("faithfulness", 1) < 1 or m.get("correctness", 1) <= 0.5 or m.get("completeness", 1) < 0.5 or m.get("relevance", 1) <= 0.5


# ==================== 构建参考答案 ====================

def build_reference(conn: Connection, client: OpenAIChatClient, case: EvalCase, gold_loader=load_gold_chunks) -> bool:
    """根据标注的相关片段让大模型写参考答案和要点，写入 case。片段已不存在或资料不能回答时返回 False。
    相关片段应先用 judge 补全，否则要点会偏少，完整性被高估。"""
    sources = gold_loader(conn, case.relevant_chunk_ids)
    if not sources:
        return False
    data = ev._parse_json(client.complete([
        {"role": "system", "content": REFERENCE_PROMPT},
        {"role": "user", "content": f"问题：{case.question}\n\n相关资料：\n\n{_refs(sources)}"},
    ]))
    case.reference_answer = str(data.get("reference_answer") or "").strip()
    case.key_points = [str(p).strip() for p in data.get("key_points") or [] if str(p).strip()] if case.reference_answer else []
    return bool(case.reference_answer)
