"""
RAG 检索链路：用户问题 → 问题向量 → 在 Milvus 中检索最相似的 k 个文档片段。

    retriever = get_retriever()
    chunks = retriever.retrieve(conn, user, "Linux 怎么安装？", kb_ids=[1], top_k=5)

1. 问题向量化（embed_question）：与文档使用同一个 embedder，问题加检索指令前缀，并与 Milvus 集合的维度做校验
2. 权限范围：只检索用户能读到的内容 —— 有知识库读权限的整库检索，只有文档授权的只检索这些文档；
   范围作为 Milvus 的过滤条件，所以返回的 k 个结果都是有权限的，不会因事后过滤变少
3. 相似度检索：余弦相似度从高到低取 top_k，可选丢弃低于 RAG_MIN_SCORE 的片段
4. 补充文档标题（引用来源用），并丢弃已删除文档残留的向量（后台删除向量前的短暂窗口）
"""
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import Connection

from app.core.config import settings
from app.core.exceptions import BizError, ErrorCode
from app.mapper import document_mapper
from app.models.user import User
from app.rag.embedder import Embedder, EmbeddingError, get_embedder
from app.rag.vector_store import MilvusVectorStore, get_vector_store
from app.services import access_service as access

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


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
        self, conn: Connection, user: User, question: str, kb_ids: list[int], top_k: int | None = None
    ) -> list[RetrievedChunk]:
        """在用户有读权限的范围内，检索与问题最相似的 top_k 个文档片段，按相似度从高到低排序。"""
        if not settings.EMBEDDING_ENABLED:
            raise _unavailable("未开启向量化（EMBEDDING_ENABLED）")
        q = normalize_question(question)
        k = top_k or self.top_k
        if not 1 <= k <= settings.RAG_TOP_K_MAX:
            raise BizError(f"top_k 必须在 1~{settings.RAG_TOP_K_MAX} 之间", ErrorCode.PARAM_INVALID)
        if not kb_ids:
            raise BizError("请选择要提问的知识库", ErrorCode.PARAM_INVALID)

        full_kb_ids, doc_ids = access.readable_scope(conn, user, kb_ids)
        if not full_kb_ids and not doc_ids:
            return []  # 这些知识库中没有用户能读的文档，不必调用向量化服务

        vector = self.embed_question(q)
        try:
            hits = self.store.search(vector, kb_ids=full_kb_ids, doc_ids=doc_ids, top_k=k)
        except Exception:
            logger.exception("Milvus 检索失败")
            raise _unavailable("向量数据库检索失败")
        if self.min_score is not None:
            hits = [h for h in hits if h.score >= self.min_score]
        if not hits:
            return []

        titles = {d["id"]: d["title"] for d in document_mapper.select_brief_by_ids(conn, list({h.doc_id for h in hits}))}
        return [
            RetrievedChunk(h.id, h.doc_id, titles[h.doc_id], h.kb_id, h.chunk_index, h.heading, h.content, h.score)
            for h in hits
            if h.doc_id in titles
        ]

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


@lru_cache
def get_retriever() -> Retriever:
    return Retriever()
