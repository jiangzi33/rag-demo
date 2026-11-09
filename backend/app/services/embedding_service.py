"""
文档切分块的向量化：文档新建后在后台把每个切分块转换成向量，写入 Milvus。

以后台任务运行（文档提交后执行），向量化服务慢或出错都不影响文档本身的保存；
任务只拿 doc_id，自己开连接重新读取文档和切分块，保证用的是已提交的最新数据。
"""
import logging
from dataclasses import dataclass

from sqlalchemy import Connection

from app.core.config import settings
from app.db.session import engine
from app.mapper import document_chunk_mapper as chunk_mapper
from app.mapper import document_mapper
from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.rag.chunker import Chunk
from app.rag.embedder import Embedder, EmbeddingError, get_embedder
from app.rag.vector_store import VectorRecord, get_vector_store

logger = logging.getLogger(__name__)


@dataclass
class ChunkVector:
    chunk: DocumentChunk
    text: str  # 实际被向量化的文本：文档标题 + 标题路径 + 块正文
    vector: list[float]


def embedding_text(doc: Document, chunk: DocumentChunk) -> str:
    return Chunk(chunk.chunk_index, chunk.content, chunk.heading_path, doc.title, doc.id, doc.kb_id).embedding_text


def embed_chunks(doc: Document, chunks: list[DocumentChunk], embedder: Embedder | None = None) -> list[ChunkVector]:
    """把文档的切分块批量转换成向量，顺序与 chunks 一致。"""
    texts = [embedding_text(doc, c) for c in chunks]
    vectors = (embedder or get_embedder()).embed_documents(texts) if texts else []
    return [ChunkVector(c, t, v) for c, t, v in zip(chunks, texts, vectors)]


def load_and_embed(conn: Connection, doc_id: int) -> tuple[Document | None, list[ChunkVector]]:
    doc = document_mapper.select_by_id(conn, doc_id)
    if not doc:  # 任务执行前文档已被删除
        return None, []
    return doc, embed_chunks(doc, chunk_mapper.select_by_doc_id(conn, doc_id))


def embed_document(doc_id: int) -> list[ChunkVector]:
    """后台任务入口：向量化文档的全部切分块并保存。失败只记日志，不抛出。"""
    if not settings.EMBEDDING_ENABLED:
        return []
    try:
        with engine.connect() as conn:
            doc, items = load_and_embed(conn, doc_id)
    except EmbeddingError:
        logger.exception("文档 %s 向量化失败", doc_id)
        return []
    if doc and items:
        try:
            save_vectors(doc, items)
        except Exception:  # Milvus 未启动、维度不符等
            logger.exception("文档 %s 的向量写入 Milvus 失败", doc_id)
            return []
    return items


def save_vectors(doc: Document, items: list[ChunkVector]) -> None:
    """写入 Milvus：按文档整篇替换。"""
    records = [
        VectorRecord(
            id=i.chunk.id,
            doc_id=doc.id,
            kb_id=doc.kb_id,
            chunk_index=i.chunk.chunk_index,
            heading=" > ".join(i.chunk.heading_path),
            content=i.chunk.content,
            vector=i.vector,
        )
        for i in items
    ]
    get_vector_store().replace_document(doc.id, records)
    logger.info("文档 %s 向量化完成：%d 个切分块，维度 %d", doc.id, len(items), len(items[0].vector))


# ---------- 删除文档 / 知识库后清理向量（后台任务） ----------

def remove_document(doc_id: int) -> None:
    if not settings.EMBEDDING_ENABLED:
        return
    try:
        get_vector_store().delete_by_doc(doc_id)
    except Exception:
        logger.exception("删除文档 %s 的向量失败", doc_id)


def remove_knowledge_base(kb_id: int) -> None:
    if not settings.EMBEDDING_ENABLED:
        return
    try:
        get_vector_store().delete_by_kb(kb_id)
    except Exception:
        logger.exception("删除知识库 %s 的向量失败", kb_id)
