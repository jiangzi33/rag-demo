"""
RAG 检索链路。

第一步：把用户问题转换成向量，且必须与向量库中文档向量处于同一个向量空间：
  - 使用与文档向量化相同的 embedder（同一个模型、同一个输出维度）
  - 问题加检索指令前缀（EMBEDDING_QUERY_INSTRUCTION），文档不加，这是 qwen3-embedding 等模型的用法
  - 与 Milvus 集合的维度做校验：向量化后改过模型或 EMBEDDING_DIMENSIONS 时直接报错，而不是返回错误的检索结果
"""
import logging
import re

from app.core.config import settings
from app.core.exceptions import BizError, ErrorCode
from app.rag.embedder import EmbeddingError, get_embedder
from app.rag.vector_store import get_vector_store

logger = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


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


def embed_question(question: str) -> list[float]:
    """把用户问题转换成与知识库文档向量维度相同的向量（已归一化）。"""
    if not settings.EMBEDDING_ENABLED:
        raise _unavailable("未开启向量化（EMBEDDING_ENABLED）")
    q = normalize_question(question)
    try:
        vector = get_embedder().embed_query(q)
    except EmbeddingError:
        logger.exception("问题向量化失败")
        raise _unavailable("向量化服务连接失败")
    try:
        dim = get_vector_store().dimension()
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
