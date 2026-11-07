"""
文档切分块的向量化：文档新建后在后台把每个切分块转换成向量。

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
        save_vectors(doc, items)
    return items


def save_vectors(doc: Document, items: list[ChunkVector]) -> None:
    """TODO: 接入向量数据库。

    每个 item 写入一条记录，建议：
      - 主键：item.chunk.id（或 f"{doc.id}:{item.chunk.chunk_index}"）
      - 向量：item.vector（已归一化，可用内积 / 余弦检索）
      - 过滤字段：doc.kb_id、doc.id（检索时限定知识库，再按用户权限过滤文档）
      - 返回给大模型的内容：item.text 或 item.chunk.content + heading_path
    写入前先按 doc.id 删除旧向量，与 document_chunks 的整篇重建保持一致。
    """
    logger.info("文档 %s 向量化完成：%d 个切分块，维度 %d", doc.id, len(items), len(items[0].vector))
