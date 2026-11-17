"""
智能问答：问题 → 大模型改写问题 → 检索最相关的 n 个文档片段 → 交给大模型整合生成回答（流式）。

    sources, rewritten = retrieve(conn, user, question, kb_ids, history, client=client)   # 在流式响应开始前完成，错误按普通接口返回
    for event in stream_answer(client, question, history, sources, rewritten.query): ...   # SSE 事件

- Query Rewrite（QA_REWRITE_ENABLED）：模糊提问、追问（如"那 Windows 呢？"）单独检索效果差，
  检索前由大模型结合对话历史改写成独立清晰的问题，并补充关键词，见 query_rewrite_service
- 改写失败或关闭时回退：检索时带上上一个问题
- 只把检索到的片段作为参考资料，要求大模型按编号 [1] [2] 标注出处，资料中没有时如实说明
- 一个片段都没检索到时不调用大模型，直接回复没有找到
"""
import json
import logging
from collections.abc import Iterator
from dataclasses import asdict

from sqlalchemy import Connection

from app.core.config import settings
from app.models.user import User
from app.rag.llm import ChatError, OpenAIChatClient
from app.services import query_rewrite_service
from app.services.query_rewrite_service import RewrittenQuery
from app.services.retrieval_service import RetrievedChunk, get_retriever, normalize_question

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是企业知识库的问答助手，只能根据用户提供的“参考资料”回答问题。
要求：
1. 只使用参考资料中的信息，不要编造；资料不足以回答时，明确说明“知识库中没有找到相关信息”，可以说明资料里有哪些相关内容。
2. 引用资料时在句末标注编号，如 [1]、[2]，编号对应参考资料的序号；不要编造不存在的编号。
3. 使用与用户问题相同的语言回答，条理清晰，可以使用 Markdown（列表、加粗、代码块）。"""

NO_RESULT_ANSWER = "知识库中没有找到与这个问题相关的内容。可以换个说法，或者确认是否选择了正确的知识库、是否有相关文档的阅读权限。"


def retrieval_query(question: str, history: list[dict]) -> str:
    """不改写时检索用的文本：带上上一个问题，让追问也能检索到相关内容。"""
    last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    if not last:
        return question
    room = settings.RAG_QUESTION_MAX_LENGTH - len(question) - 1
    return f"{last[-room:]}\n{question}" if room > 0 else question


def retrieve(
    conn: Connection, user: User, question: str, kb_ids: list[int], history: list[dict], top_k: int | None = None,
    client: OpenAIChatClient | None = None,
) -> tuple[list[RetrievedChunk], RewrittenQuery | None]:
    """返回检索到的片段和改写结果。传入 client 且开启 QA_REWRITE_ENABLED 时先改写问题，
    改写后的问题用于语义检索，原问题 + 改写 + 关键词用于关键词检索；未改写（关闭或失败）时改写结果为 None。"""
    q = normalize_question(question)  # 先校验，空问题不调用大模型
    rewritten = query_rewrite_service.rewrite(client, q, history) if client and settings.QA_REWRITE_ENABLED else None
    k = top_k or settings.QA_TOP_K
    if rewritten:
        sources = get_retriever().retrieve(conn, user, rewritten.query, kb_ids, k, keyword_text=rewritten.keyword_text(q))
    else:
        sources = get_retriever().retrieve(conn, user, retrieval_query(q, history), kb_ids, k)
    return sources, rewritten


def build_messages(question: str, history: list[dict], sources: list[RetrievedChunk]) -> list[dict]:
    refs = "\n\n".join(
        f"[{i}] 《{c.doc_title}》{(' > ' + c.heading) if c.heading else ''}\n{c.content}" for i, c in enumerate(sources, 1)
    )
    recent = history[-settings.QA_HISTORY_MESSAGES:] if settings.QA_HISTORY_MESSAGES > 0 else []
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *({"role": m["role"], "content": m["content"]} for m in recent),
        {"role": "user", "content": f"参考资料：\n\n{refs}\n\n---\n问题：{question}"},
    ]


def _event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_answer(
    client: OpenAIChatClient | None, question: str, history: list[dict], sources: list[RetrievedChunk],
    rewritten_query: str | None = None,
) -> Iterator[str]:
    """SSE 事件流：
    {"type": "sources", "sources": [...], "query": "..."}  参考片段，序号即回答中的 [n]；
                                           query 为改写后用于检索的问题，与原问题相同或未改写时不返回
    {"type": "delta", "text": "..."}       回答的增量文字
    {"type": "error", "message": "..."}    生成中途出错（已输出的部分保留）
    {"type": "done"}
    """
    event = {"type": "sources", "sources": [asdict(c) for c in sources]}
    if rewritten_query and rewritten_query != question:
        event["query"] = rewritten_query
    yield _event(event)
    if not sources:
        yield _event({"type": "delta", "text": NO_RESULT_ANSWER})
        yield _event({"type": "done"})
        return
    try:
        for text in client.stream(build_messages(question, history, sources)):
            yield _event({"type": "delta", "text": text})
    except ChatError as e:
        logger.exception("大模型生成失败")
        yield _event({"type": "error", "message": f"回答生成失败：{e}"})
        return
    yield _event({"type": "done"})
