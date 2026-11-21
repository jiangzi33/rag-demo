"""
DocumentSummaryMapper —— document_summaries 表的全部 SQL。

每篇文档的每个关注点只保留一条摘要：重新生成时整条替换。
"""
import json

from sqlalchemy import Connection, text

from app.models.document_summary import DocumentSummary

BASE_COLUMN_LIST = "id, doc_id, kb_id, focus, fingerprint, mode, stale, truncated, sources, summary, model, created_at"


# ==================== 查询 select ====================

def select_by_doc_focus(conn: Connection, doc_id: int, focus: str) -> DocumentSummary | None:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM document_summaries WHERE doc_id = :doc_id AND focus = :focus"
    row = conn.execute(text(sql), {"doc_id": doc_id, "focus": focus}).mappings().first()
    return DocumentSummary(**row) if row else None


# ==================== 写入 insert ====================

def replace(
    conn: Connection, *, doc_id: int, kb_id: int, focus: str, fingerprint: str, mode: str, stale: bool, truncated: bool,
    sources: list[dict], summary: str, model: str,
) -> None:
    """删除该文档该关注点的旧摘要后插入新摘要（MySQL / SQLite 通用的 upsert），由调用方提交。"""
    conn.execute(text("DELETE FROM document_summaries WHERE doc_id = :doc_id AND focus = :focus"), {"doc_id": doc_id, "focus": focus})
    sql = """
        INSERT INTO document_summaries (doc_id, kb_id, focus, fingerprint, mode, stale, truncated, sources, summary, model)
        VALUES (:doc_id, :kb_id, :focus, :fingerprint, :mode, :stale, :truncated, :sources, :summary, :model)
    """
    conn.execute(text(sql), {
        "doc_id": doc_id, "kb_id": kb_id, "focus": focus, "fingerprint": fingerprint, "mode": mode,
        "stale": int(stale), "truncated": int(truncated), "sources": json.dumps(sources, ensure_ascii=False),
        "summary": summary, "model": model,
    })


# ==================== 删除 delete ====================

def delete_by_doc_id(conn: Connection, doc_id: int) -> int:
    sql = "DELETE FROM document_summaries WHERE doc_id = :doc_id"
    return conn.execute(text(sql), {"doc_id": doc_id}).rowcount


def delete_by_kb_id(conn: Connection, kb_id: int) -> int:
    """删除知识库时级联删除其下所有摘要。"""
    sql = "DELETE FROM document_summaries WHERE kb_id = :kb_id"
    return conn.execute(text(sql), {"kb_id": kb_id}).rowcount
