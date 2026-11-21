"""
文档摘要：基于 RAG 链路的切分块生成文档摘要，每个要点标注出处片段 [n]，点击可跳转到正文高亮（流式）。

    fp = fingerprint(conn, doc)
    if cached := get_cached(conn, doc, focus, fp):             # 文档没变时直接返回已保存的摘要，不调用大模型
        events = stream_cached(cached)
    else:
        sources, plan = prepare(conn, user, doc, focus)        # 在流式响应开始前完成，错误按普通接口返回
        events = stream_summary(client, doc, sources, plan, focus, on_complete=partial(save, doc, focus, fp, ...))  # SSE 事件

三种方式（plan）：
- full：切分块总字数不超过 SUMMARY_DIRECT_CHARS，全部按顺序一次交给大模型
- map_reduce：长文档按 SUMMARY_BATCH_CHARS 分批，每批先提炼要点（map，非流式），再把各批要点合并成最终摘要（reduce，流式）；
  要点仍超长时继续分批提炼。片段编号在整篇文档内统一，各阶段保留编号，最终摘要的 [n] 仍对应原片段
- focus：指定了关注点（如“部署步骤”“性能相关的结论”），在这篇文档内混合检索最相关的 SUMMARY_FOCUS_TOP_K 个片段，
  按文档顺序排列后只总结这部分内容

文档编辑后切分块不会自动重建（scripts/reindex.py 重建），切分块与正文不一致时改用当前正文临时切分（stale=True），
此时片段没有 id，出处只能定位到文档，不能高亮。

摘要保存到 document_summaries（每篇文档每个关注点一条），避免重复生成：
- 失效：记录生成时文档标题、正文和切分块 id 的指纹（fingerprint），文档编辑或重建切分块后指纹变化，下次请求重新生成
  （切分块 id 变化后旧摘要的出处链接会失效，所以也计入指纹）
- 只保存完整生成的摘要；中途出错、用户停止或没有内容可总结时不保存
- 用户点“重新生成”（refresh）时跳过缓存，生成后覆盖旧摘要
"""
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import asdict

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.session import engine
from app.mapper import document_chunk_mapper as chunk_mapper
from app.mapper import document_summary_mapper as summary_mapper
from app.models.document import Document
from app.models.document_summary import DocumentSummary
from app.models.user import User
from app.rag.chunker import DocumentChunker
from app.rag.llm import ChatError, OpenAIChatClient
from app.services.retrieval_service import RetrievedChunk, get_retriever, normalize_question

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是企业知识库的文档摘要助手，根据给出的文档内容写摘要。
要求：
1. 只使用给出的内容，不要编造或补充文档以外的信息。
2. 先用一两句话概括文档的主题和用途，再按文档结构分点列出关键内容（结论、步骤、配置、数据、注意事项等），篇幅与文档长度相称。
3. 每个要点在句末标注来源片段编号，如 [1]、[2]，编号对应给出内容中的序号；不要编造不存在的编号。
4. 使用与文档相同的语言，可以使用 Markdown（小标题、列表、加粗）。"""

FOCUS_PROMPT = "\n5. 用户只关心：{focus}。只总结与之相关的内容，不相关的部分不要写；给出的内容中没有相关信息时如实说明。"

MAP_PROMPT = """你在为一篇长文档分段提炼要点，这是其中的第 {i}/{n} 部分。
1. 用简洁的列表列出这部分的关键信息，保留重要的事实、数据、步骤、结论和注意事项，不要遗漏。
2. 每个要点句末保留来源片段编号，如 [3]，编号与给出内容中的一致（内容本身是要点时，沿用其中的编号）。
3. 只使用给出的内容，不要编造。只输出要点列表，不要开头语和总结。"""

EMPTY_ANSWER = "这篇文档还没有可以总结的内容。"
NO_FOCUS_ANSWER = "这篇文档中没有找到与关注点相关的内容，可以换个说法，或者不填关注点总结全文。"

_chunker = DocumentChunker()


# ==================== 准备片段 ====================

def doc_chunks(conn: Connection, doc: Document) -> tuple[list[RetrievedChunk], bool]:
    """文档的全部切分块（按文档顺序），以及是否为临时切分。
    按当前正文重新切分（不写库）与已存的切分块比较，不一致说明文档编辑后还没有重建切分块，改用临时切分的结果。"""
    stored = chunk_mapper.select_by_doc_id(conn, doc.id)
    fresh = _chunker.split_document(doc)
    if [(c.content, c.heading_path) for c in stored] == [(c.content, c.heading_path) for c in fresh]:
        return [
            RetrievedChunk(c.id, c.doc_id, doc.title, c.kb_id, c.chunk_index, " > ".join(c.heading_path), c.content, 1.0)
            for c in stored
        ], False
    return [
        RetrievedChunk(0, doc.id, doc.title, doc.kb_id, c.index, " > ".join(c.heading_path), c.content, 1.0) for c in fresh
    ], bool(fresh)


def prepare(conn: Connection, user: User, doc: Document, focus: str | None = None) -> tuple[list[RetrievedChunk], dict]:
    """返回交给大模型的片段和摘要方式 {"mode": full / map_reduce / focus, "stale": bool, "truncated": bool}。
    调用方需已校验文档读权限。"""
    if focus:
        q = normalize_question(focus)
        chunks, stale = doc_chunks(conn, doc)
        if stale:  # 向量库中还是旧内容，检索不到新正文，退化为全文摘要 + 关注点
            sources = chunks
        else:
            hits = get_retriever().retrieve(conn, user, q, [doc.kb_id], settings.SUMMARY_FOCUS_TOP_K, within_doc_ids=[doc.id])
            sources = sorted(hits, key=lambda c: c.chunk_index)  # 按文档顺序，便于大模型理解上下文
        mode = "focus"
    else:
        sources, stale = doc_chunks(conn, doc)
        mode = "full" if _chars(sources) <= settings.SUMMARY_DIRECT_CHARS else "map_reduce"
    truncated = len(sources) > settings.SUMMARY_MAX_CHUNKS
    sources = sources[: settings.SUMMARY_MAX_CHUNKS]
    if mode == "focus" and _chars(sources) > settings.SUMMARY_DIRECT_CHARS:
        mode = "map_reduce"  # 临时切分时关注点也要处理全文
    return sources, {"mode": mode, "stale": stale, "truncated": truncated}


def _chars(sources: list[RetrievedChunk]) -> int:
    return sum(len(c.content) for c in sources)


def _ref(n: int, c: RetrievedChunk) -> str:
    return f"[{n}]{(' ' + c.heading) if c.heading else ''}\n{c.content}"


def batches(texts: list[str], max_chars: int) -> list[list[str]]:
    """按顺序把文本分成每批不超过 max_chars 字的若干批（单条超长时独占一批）。"""
    result, current, size = [], [], 0
    for t in texts:
        if current and size + len(t) > max_chars:
            result.append(current)
            current, size = [], 0
        current.append(t)
        size += len(t)
    if current:
        result.append(current)
    return result


# ==================== 缓存 ====================

def normalize_focus(focus: str | None) -> str:
    """关注点折叠空白后作为缓存的键，空字符串为全文摘要；过长时报错（同问题的长度校验）。"""
    return normalize_question(focus) if focus and focus.strip() else ""


def fingerprint(conn: Connection, doc: Document) -> str:
    ids = [c.id for c in chunk_mapper.select_by_doc_id(conn, doc.id)]
    return hashlib.sha256(json.dumps([doc.title, doc.content, ids], ensure_ascii=False).encode()).hexdigest()


def get_cached(conn: Connection, doc: Document, focus: str, fp: str) -> DocumentSummary | None:
    """已保存且文档没有变化的摘要。"""
    saved = summary_mapper.select_by_doc_focus(conn, doc.id, focus)
    return saved if saved and saved.fingerprint == fp else None


def save(
    doc: Document, focus: str, fp: str, plan: dict, sources: list[RetrievedChunk], summary: str, model: str,
) -> None:
    """生成完成后保存摘要。在流式响应中调用，请求的数据库连接可能已关闭，使用新连接；保存失败不影响已返回的摘要。"""
    # 出处只需要定位信息，不保存片段正文
    refs = [{**asdict(c), "content": ""} for c in sources]
    try:
        with engine.connect() as conn:
            summary_mapper.replace(
                conn, doc_id=doc.id, kb_id=doc.kb_id, focus=focus, fingerprint=fp, mode=plan["mode"],
                stale=plan["stale"], truncated=plan["truncated"], sources=refs, summary=summary, model=model,
            )
            conn.commit()
    except IntegrityError:
        logger.info("文档 %s 的摘要已由另一个请求同时保存", doc.id)  # 两个请求同时生成，保留先提交的
    except Exception:
        logger.exception("文档 %s 的摘要保存失败", doc.id)


# ==================== 生成 ====================

def _event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_messages(doc: Document, content: str, focus: str | None = None, condensed: bool = False) -> list[dict]:
    system = SYSTEM_PROMPT + (FOCUS_PROMPT.format(focus=focus) if focus else "")
    what = "文档各部分的要点（编号对应原文片段）" if condensed else "文档内容（按顺序切分为片段）"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"文档标题：《{doc.title}》\n\n{what}：\n\n{content}\n\n---\n请写出这篇文档的摘要。"},
    ]


def stream_cached(saved: DocumentSummary) -> Iterator[str]:
    """已保存的摘要，事件格式同 stream_summary，一次性返回全文。"""
    yield _event({
        "type": "sources", "sources": saved.sources, "mode": saved.mode, "stale": saved.stale, "truncated": saved.truncated,
        "cached": True, "created_at": saved.created_at.isoformat(sep=" ", timespec="seconds"), "model": saved.model,
    })
    yield _event({"type": "delta", "text": saved.summary})
    yield _event({"type": "done"})


def stream_summary(
    client: OpenAIChatClient, doc: Document, sources: list[RetrievedChunk], plan: dict, focus: str | None = None,
    on_complete: Callable[[str], None] | None = None,
) -> Iterator[str]:
    """SSE 事件流（on_complete：完整生成后以摘要全文调用，用于保存）：
    {"type": "sources", "sources": [...], "mode": "...", "stale": bool, "truncated": bool, "cached": false}
                                                  片段，序号即摘要中的 [n]；cached 为 true 时另有 created_at、model
    {"type": "progress", "done": 1, "total": 5}   map_reduce 分批提炼的进度
    {"type": "delta", "text": "..."}              摘要的增量文字
    {"type": "error", "message": "..."}           生成中途出错（已输出的部分保留）
    {"type": "done"}
    """
    yield _event({"type": "sources", "sources": [asdict(c) for c in sources], **plan, "cached": False})
    if not sources:
        yield _event({"type": "delta", "text": NO_FOCUS_ANSWER if focus else EMPTY_ANSWER})
        yield _event({"type": "done"})
        return
    texts = [_ref(n, c) for n, c in enumerate(sources, 1)]
    try:
        condensed = False
        if plan["mode"] == "map_reduce":
            # 要点合起来仍超长时继续分批提炼，直到能一次交给大模型（最多 3 轮，避免要点不缩短时反复调用）
            for _ in range(3):
                if sum(len(t) for t in texts) <= settings.SUMMARY_DIRECT_CHARS or len(texts) <= 1:
                    break
                groups = batches(texts, settings.SUMMARY_BATCH_CHARS)
                texts = []
                for i, group in enumerate(groups, 1):
                    yield _event({"type": "progress", "done": i - 1, "total": len(groups)})
                    texts.append(client.complete([
                        {"role": "system", "content": MAP_PROMPT.format(i=i, n=len(groups))},
                        {"role": "user", "content": f"文档标题：《{doc.title}》\n\n" + "\n\n".join(group)},
                    ]))
                yield _event({"type": "progress", "done": len(groups), "total": len(groups)})
                condensed = True
        parts = []
        for text in client.stream(build_messages(doc, "\n\n".join(texts), focus, condensed)):
            parts.append(text)
            yield _event({"type": "delta", "text": text})
    except ChatError as e:
        logger.exception("文档 %s 摘要生成失败", doc.id)
        yield _event({"type": "error", "message": f"摘要生成失败：{e}"})
        return
    if on_complete and "".join(parts).strip():
        on_complete("".join(parts))
    yield _event({"type": "done"})
