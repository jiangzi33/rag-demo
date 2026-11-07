"""
文档切分块的维护：文档新建 / 编辑后重新切分并写入 document_chunks，供 RAG 检索使用。

不单独提交事务，由调用方与文档本身的写入一起提交，保证文档和切分块始终一致。
"""
from sqlalchemy import Connection

from app.mapper import document_chunk_mapper as chunk_mapper
from app.models.document import Document
from app.rag.chunker import DocumentChunker

_chunker = DocumentChunker()


def rebuild(conn: Connection, doc: Document) -> int:
    """删除文档原有的切分块，按当前标题和正文重新切分，返回新块数。"""
    chunk_mapper.delete_by_doc_id(conn, doc.id)
    return chunk_mapper.insert_batch(conn, _chunker.split_document(doc))
