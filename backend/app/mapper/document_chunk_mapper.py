"""
DocumentChunkMapper —— document_chunks 表的全部 SQL。

切分块不做局部修改：文档新建 / 编辑时整篇删除后重新插入。
"""
import json

from sqlalchemy import Connection, text

from app.models.document_chunk import DocumentChunk
from app.rag.chunker import Chunk

BASE_COLUMN_LIST = "id, doc_id, kb_id, chunk_index, heading_path, content, created_at"


# ==================== 查询 select ====================

def select_by_id(conn: Connection, id: int) -> DocumentChunk | None:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM document_chunks WHERE id = :id"
    row = conn.execute(text(sql), {"id": id}).mappings().first()
    return DocumentChunk(**row) if row else None


def select_by_doc_id(conn: Connection, doc_id: int) -> list[DocumentChunk]:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM document_chunks WHERE doc_id = :doc_id ORDER BY chunk_index"
    return [DocumentChunk(**row) for row in conn.execute(text(sql), {"doc_id": doc_id}).mappings().all()]


# ==================== 新增 insert ====================

def insert_batch(conn: Connection, chunks: list[Chunk]) -> int:
    """批量插入（executemany），返回插入条数。"""
    if not chunks:
        return 0
    sql = """
        INSERT INTO document_chunks (doc_id, kb_id, chunk_index, heading_path, content)
        VALUES (:doc_id, :kb_id, :chunk_index, :heading_path, :content)
    """
    params = [
        {
            "doc_id": c.doc_id,
            "kb_id": c.kb_id,
            "chunk_index": c.index,
            "heading_path": json.dumps(c.heading_path, ensure_ascii=False),
            "content": c.content,
        }
        for c in chunks
    ]
    conn.execute(text(sql), params)
    return len(params)


# ==================== 删除 delete ====================

def delete_by_doc_id(conn: Connection, doc_id: int) -> int:
    sql = "DELETE FROM document_chunks WHERE doc_id = :doc_id"
    return conn.execute(text(sql), {"doc_id": doc_id}).rowcount


def delete_by_kb_id(conn: Connection, kb_id: int) -> int:
    """删除知识库时级联删除其下所有切分块。"""
    sql = "DELETE FROM document_chunks WHERE kb_id = :kb_id"
    return conn.execute(text(sql), {"kb_id": kb_id}).rowcount
