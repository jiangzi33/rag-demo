"""
DocumentMapper —— documents 表的全部 SQL，写法与 user_mapper / knowledge_base_mapper 一致。

列表查询不查 content（正文可能很大），只有详情才查正文：
    BRIEF_COLUMN_LIST  <->  列表用的 <sql id="Brief_Column_List">
    BASE_COLUMN_LIST   <->  详情用的 <sql id="Base_Column_List">
"""
from sqlalchemy import Connection, RowMapping, text

from app.mapper.user_mapper import _escape_like
from app.models.document import Document

BRIEF_COLUMN_LIST = "id, kb_id, title, author_id, published_at, updated_at"
BASE_COLUMN_LIST = "id, kb_id, title, content, author_id, published_at, updated_at"


def _row_to_doc(row: RowMapping | None) -> Document | None:
    return Document(**row) if row else None


# ==================== 查询 select ====================

def select_by_id(conn: Connection, id: int) -> Document | None:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM documents WHERE id = :id"
    return _row_to_doc(conn.execute(text(sql), {"id": id}).mappings().first())


def _build_where(kb_id: int, keyword: str | None) -> tuple[str, dict]:
    """
    <where>
      kb_id = #{kbId}
      <if test="keyword != null and keyword != ''"> AND title LIKE #{kw} </if>
    </where>
    """
    conditions, params = ["kb_id = :kb_id"], {"kb_id": kb_id}
    if keyword and keyword.strip():
        conditions.append("title LIKE :kw ESCAPE '!'")
        params["kw"] = f"%{_escape_like(keyword.strip())}%"
    return " WHERE " + " AND ".join(conditions), params


def count_by_kb(conn: Connection, kb_id: int, keyword: str | None) -> int:
    where, params = _build_where(kb_id, keyword)
    return conn.execute(text(f"SELECT COUNT(*) FROM documents{where}"), params).scalar_one()


def select_page_by_kb(conn: Connection, kb_id: int, keyword: str | None, offset: int, limit: int) -> list[dict]:
    """返回不含 content 的行（dict），按最近编辑倒序。"""
    where, params = _build_where(kb_id, keyword)
    sql = (
        f"SELECT {BRIEF_COLUMN_LIST} FROM documents{where} "
        "ORDER BY updated_at DESC, id DESC LIMIT :limit OFFSET :offset"
    )
    rows = conn.execute(text(sql), {**params, "limit": limit, "offset": offset}).mappings().all()
    return [dict(row) for row in rows]


# ==================== 新增 insert ====================

def insert(conn: Connection, kb_id: int, title: str, content: str, author_id: int) -> int:
    """插入文档，返回自增主键 id。published_at / updated_at 使用数据库默认值。"""
    sql = """
        INSERT INTO documents (kb_id, title, content, author_id)
        VALUES (:kb_id, :title, :content, :author_id)
    """
    params = {"kb_id": kb_id, "title": title, "content": content, "author_id": author_id}
    return conn.execute(text(sql), params).lastrowid


# ==================== 修改 update ====================

def update_content(conn: Connection, id: int, title: str, content: str) -> int:
    """编辑标题和正文，同时刷新最近编辑日期；作者和发布日期保持不变。"""
    sql = """
        UPDATE documents SET title = :title, content = :content, updated_at = CURRENT_TIMESTAMP
        WHERE id = :id
    """
    return conn.execute(text(sql), {"title": title, "content": content, "id": id}).rowcount


# ==================== 删除 delete ====================

def delete_by_id(conn: Connection, id: int) -> int:
    sql = "DELETE FROM documents WHERE id = :id"
    return conn.execute(text(sql), {"id": id}).rowcount


def delete_by_kb_id(conn: Connection, kb_id: int) -> int:
    """删除知识库时级联删除其下所有文档。"""
    sql = "DELETE FROM documents WHERE kb_id = :kb_id"
    return conn.execute(text(sql), {"kb_id": kb_id}).rowcount
