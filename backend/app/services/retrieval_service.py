"""
RAG 检索链路：用户问题 → 语义检索 + 关键词检索 → 融合排序后取最相关的 k 个文档片段。

    retriever = get_retriever()
    chunks = retriever.retrieve(conn, user, "Linux 怎么安装？", kb_ids=[1], top_k=5)

1. 问题向量化（embed_question）：与文档使用同一个 embedder，问题加检索指令前缀，并与 Milvus 集合的维度做校验
2. 权限范围：只检索用户能读到的内容 —— 有知识库读权限的整库检索，只有文档授权的只检索这些文档；
   范围作为 Milvus 的过滤条件，所以返回的 k 个结果都是有权限的，不会因事后过滤变少
3. 混合检索（RAG_SEARCH_MODE=hybrid）：
   - 语义检索：问题向量与片段向量的余弦相似度，擅长同义改写、自然语言描述
   - 关键词检索：Milvus BM25 全文检索（jieba 分词），擅长专有名词、术语、型号、错误码等必须字面匹配的内容；
     只靠语义检索时，这类术语常被语义相近但不相关的片段挤掉，大模型拿不到正确资料就容易编造
   - 两路各取 RAG_HYBRID_CANDIDATES 个候选，按排名做 RRF 融合（两路都靠前的排最前），取 top_k
   可选丢弃余弦相似度低于 RAG_MIN_SCORE 的片段，命中关键词的片段不受此限制
4. 补充文档标题（引用来源用），并丢弃已删除文档残留的向量（后台删除向量前的短暂窗口）
5. 日志（RAG_LOG_RESULTS）：每次检索打印问题、范围、两路候选数、耗时，以及每条结果的相似度、BM25 得分和两路排名
"""
import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import Connection

from app.core.config import settings
from app.core.exceptions import BizError, ErrorCode
from app.mapper import document_mapper
from app.models.user import User
from app.rag.embedder import Embedder, EmbeddingError, get_embedder
from app.rag.vector_store import MilvusVectorStore, SearchHit, get_vector_store
from app.services import access_service as access

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w]+")  # 标点符号也会被分词成词元，关键词检索前去掉


@dataclass
class RetrievedChunk:
    chunk_id: int  # document_chunks.id
    doc_id: int
    doc_title: str
    kb_id: int
    chunk_index: int
    heading: str  # 章节标题路径，如 "安装 > Linux"
    content: str
    score: float  # 余弦相似度，越大越相关
    keyword_score: float | None = None  # BM25 得分；None 表示没有命中关键词，只是语义相近


def _unavailable(reason: str) -> BizError:
    return BizError(f"知识库问答暂不可用：{reason}", ErrorCode.RAG_UNAVAILABLE, 503)


def normalize_question(question: str) -> str:
    """折叠空白（含换行）并校验长度。"""
    q = _WHITESPACE.sub(" ", question or "").strip()
    if not q:
        raise BizError("问题不能为空", ErrorCode.PARAM_INVALID)
    if len(q) > settings.RAG_QUESTION_MAX_LENGTH:
        raise BizError(f"问题不能超过 {settings.RAG_QUESTION_MAX_LENGTH} 个字符", ErrorCode.PARAM_INVALID)
    return q


class Retriever:
    def __init__(
        self,
        embedder: Embedder | None = None,
        vector_store: MilvusVectorStore | None = None,
        top_k: int | None = None,
        min_score: float | None = None,
    ):
        # 不传时使用全局共用的实例，第一次用到时才创建（Ollama / Milvus 未启动不影响应用启动）
        self._embedder = embedder
        self._store = vector_store
        self.top_k = top_k or settings.RAG_TOP_K
        self.min_score = settings.RAG_MIN_SCORE if min_score is None else min_score

    @property
    def embedder(self) -> Embedder:
        return self._embedder or get_embedder()

    @property
    def store(self) -> MilvusVectorStore:
        return self._store or get_vector_store()

    # ---------- 对外接口 ----------

    def retrieve(
        self, conn: Connection, user: User, question: str, kb_ids: list[int], top_k: int | None = None,
        keyword_text: str | None = None, within_doc_ids: list[int] | None = None,
    ) -> list[RetrievedChunk]:
        """在用户有读权限的范围内，检索与问题最相似的 top_k 个文档片段，按相似度从高到低排序。
        keyword_text：关键词检索使用的文本（如问题改写后附加的同义词），不填使用 question。
        within_doc_ids：只在这些文档中检索（如文档摘要），调用方需已校验这些文档的读权限。"""
        if not settings.EMBEDDING_ENABLED:
            raise _unavailable("未开启向量化（EMBEDDING_ENABLED）")
        q = normalize_question(question)
        k = top_k or self.top_k
        if not 1 <= k <= settings.RAG_TOP_K_MAX:
            raise BizError(f"top_k 必须在 1~{settings.RAG_TOP_K_MAX} 之间", ErrorCode.PARAM_INVALID)
        if not kb_ids:
            raise BizError("请选择要提问的知识库", ErrorCode.PARAM_INVALID)

        if within_doc_ids is not None:
            full_kb_ids, doc_ids = [], list(within_doc_ids)
        else:
            full_kb_ids, doc_ids = access.readable_scope(conn, user, kb_ids)
        if not full_kb_ids and not doc_ids:
            if settings.RAG_LOG_RESULTS:
                logger.info('检索 user=%s query="%s" kb_ids=%s：没有可读的文档，返回 0 条', user.id, q, kb_ids)
            return []  # 这些知识库中没有用户能读的文档，不必调用向量化服务

        started = time.perf_counter()
        vector = self.embed_question(q)
        embedded = time.perf_counter()
        hybrid = settings.RAG_SEARCH_MODE == "hybrid"
        n = max(k, settings.RAG_HYBRID_CANDIDATES) if hybrid else k
        try:
            semantic = self.store.search(vector, kb_ids=full_kb_ids, doc_ids=doc_ids, top_k=n)
            keyword = self.store.keyword_search(keyword_query(keyword_text or q), kb_ids=full_kb_ids, doc_ids=doc_ids, top_k=n) if hybrid else []
        except Exception:
            logger.exception("Milvus 检索失败")
            raise _unavailable("向量数据库检索失败")
        searched = time.perf_counter()

        chunks = fuse(semantic, keyword, vector)
        fused = len(chunks)
        if self.min_score is not None:
            chunks = [c for c in chunks if c.score >= self.min_score or c.keyword_score is not None]
        below_min = fused - len(chunks)
        # 多取一些再截断：标题补充时会丢掉已删除文档的片段
        chunks = chunks[: k * 2]
        result = []
        if chunks:
            titles = {d["id"]: d["title"] for d in document_mapper.select_brief_by_ids(conn, list({c.doc_id for c in chunks}))}
            for c in chunks:
                if c.doc_id in titles:
                    c.doc_title = titles[c.doc_id]
                    result.append(c)
        result = result[:k]

        if settings.RAG_LOG_RESULTS:
            _log_results(
                user, q, keyword_text, full_kb_ids, doc_ids, k, semantic, keyword, result, below_min,
                {"向量化": embedded - started, "检索": searched - embedded, "总计": time.perf_counter() - started},
            )
        return result

    def embed_question(self, question: str) -> list[float]:
        """把用户问题转换成与知识库文档向量维度相同的向量（已归一化）。"""
        q = normalize_question(question)
        try:
            vector = self.embedder.embed_query(q)
        except EmbeddingError:
            logger.exception("问题向量化失败")
            raise _unavailable("向量化服务连接失败")
        try:
            dim = self.store.dimension()
        except Exception:  # Milvus 未启动等
            logger.exception("读取 Milvus 集合维度失败")
            raise _unavailable("向量数据库连接失败")
        # dim 为 None：还没有任何文档完成向量化，检索结果为空，不需要校验
        if dim is not None and len(vector) != dim:
            logger.error("问题向量 %d 维，Milvus 集合 %s 为 %d 维", len(vector), settings.MILVUS_COLLECTION, dim)
            raise _unavailable(
                f"问题向量为 {len(vector)} 维，知识库向量为 {dim} 维；请检查向量模型配置，或删除集合后重新向量化全部文档"
            )
        return vector


def _log_results(
    user: User, query: str, keyword_text: str | None, kb_ids: list[int], doc_ids: list[int], top_k: int,
    semantic: list[SearchHit], keyword: list[SearchHit], result: list[RetrievedChunk],
    below_min: int, timings: dict[str, float],
) -> None:
    """一次检索打印一条汇总 + 每条结果一行，例如：
    检索 user=1 mode=hybrid top_k=5 query="InnoDB 怎么实现事务" 范围 知识库[1, 2] 文档[]
        候选 语义 20 / 关键词 7 → 返回 5 条 | 向量化 85ms 检索 12ms 总计 103ms
      #1 chunk=812 《MySQL数据库技术原理》 > 事务 > ACID | 相似度 0.712 BM25 6.31 | 语义#2 关键词#1 | 正文预览...
    """
    sem_rank = {h.id: i for i, h in enumerate(semantic, 1)}
    kw_rank = {h.id: i for i, h in enumerate(keyword, 1)}
    extra = f"，{below_min} 条低于相似度下限被丢弃" if below_min else ""
    lines = [
        f'检索 user={user.id} mode={settings.RAG_SEARCH_MODE} top_k={top_k} query="{query}" '
        + (f'keyword_text="{keyword_text}" ' if keyword_text else "")
        + f"范围 知识库{kb_ids} 文档{doc_ids}",
        f"    候选 语义 {len(semantic)} / 关键词 {len(keyword)} → 返回 {len(result)} 条{extra} | "
        + " ".join(f"{name} {seconds * 1000:.0f}ms" for name, seconds in timings.items()),
    ]
    width = settings.RAG_LOG_CONTENT_CHARS
    for i, c in enumerate(result, 1):
        title = f"《{c.doc_title}》" + (f" > {c.heading}" if c.heading else "")
        bm25 = f" BM25 {c.keyword_score:.2f}" if c.keyword_score is not None else ""
        ranks = f"语义#{sem_rank.get(c.chunk_id, '-')} 关键词#{kw_rank.get(c.chunk_id, '-')}"
        line = f"  #{i} chunk={c.chunk_id} doc={c.doc_id} {title} | 相似度 {c.score:.3f}{bm25} | {ranks}"
        if width > 0:
            preview = _WHITESPACE.sub(" ", c.content).strip()
            line += f" | {preview[:width]}{'…' if len(preview) > width else ''}"
        lines.append(line)
    logger.info("\n".join(lines))


def keyword_query(question: str) -> str:
    return _NON_WORD.sub(" ", question).strip()


def fuse(semantic: list[SearchHit], keyword: list[SearchHit], query_vector: list[float]) -> list[RetrievedChunk]:
    """RRF（Reciprocal Rank Fusion）融合两路结果：score = Σ 权重 / (RAG_RRF_K + 排名)。
    只用排名不用原始分数，余弦相似度和 BM25 得分量纲不同也能直接融合。返回按融合得分从高到低排序的片段。"""
    fused: dict[int, float] = {}
    chunks: dict[int, RetrievedChunk] = {}
    for rank, h in enumerate(semantic, 1):
        fused[h.id] = 1 / (settings.RAG_RRF_K + rank)
        chunks[h.id] = RetrievedChunk(h.id, h.doc_id, "", h.kb_id, h.chunk_index, h.heading, h.content, h.score)
    for rank, h in enumerate(keyword, 1):
        fused[h.id] = fused.get(h.id, 0) + settings.RAG_KEYWORD_WEIGHT / (settings.RAG_RRF_K + rank)
        if h.id not in chunks:  # 只命中关键词：用片段向量算出与问题的余弦相似度（向量均已归一化，点积即余弦）
            cosine = sum(a * b for a, b in zip(h.vector, query_vector)) if h.vector else 0.0
            chunks[h.id] = RetrievedChunk(h.id, h.doc_id, "", h.kb_id, h.chunk_index, h.heading, h.content, cosine)
        chunks[h.id].keyword_score = h.score
    return [chunks[i] for i in sorted(fused, key=lambda i: -fused[i])]


@lru_cache
def get_retriever() -> Retriever:
    return Retriever()
