"""
KnowledgeBaseMapper —— knowledge_bases 表的全部 SQL，写法与 user_mapper 一致。

updated_at 不依赖 MySQL 的 ON UPDATE CURRENT_TIMESTAMP（SQLite 不支持），
而是在每条 UPDATE 语句里显式写 updated_at = CURRENT_TIMESTAMP，保证两种数据库行为一致。
"""
from sqlalchemy import Connection, RowMapping, text

from app.mapper.user_mapper import _escape_like
from app.models.knowledge_base import KnowledgeBase

BASE_COLUMN_LIST = "id, name, owner_id, created_at, updated_at"


def _row_to_kb(row: RowMapping | None) -> KnowledgeBase | None:
    return KnowledgeBase(**row) if row else None


# ==================== 查询 select ====================

def select_by_id(conn: Connection, id: int) -> KnowledgeBase | None:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM knowledge_bases WHERE id = :id"
    return _row_to_kb(conn.execute(text(sql), {"id": id}).mappings().first())


def select_by_owner_and_name(conn: Connection, owner_id: int, name: str) -> KnowledgeBase | None:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM knowledge_bases WHERE owner_id = :owner_id AND name = :name"
    return _row_to_kb(conn.execute(text(sql), {"owner_id": owner_id, "name": name}).mappings().first())


def select_by_ids(conn: Connection, ids: list[int]) -> list[KnowledgeBase]:
    if not ids:
        return []
    params = {f"id{i}": v for i, v in enumerate(ids)}
    sql = f"SELECT {BASE_COLUMN_LIST} FROM knowledge_bases WHERE id IN ({', '.join(':' + k for k in params)})"
    return [KnowledgeBase(**row) for row in conn.execute(text(sql), params).mappings().all()]


def _build_where(user_id: int | None, keyword: str | None) -> tuple[str, dict]:
    """
    user_id 为 None 时查询全部知识库；否则只查用户有权限的：自己是拥有者，或对知识库 / 其中某篇文档有已生效的授权。
    <where>
      <if test="userId != null"> (owner_id = #{userId} OR id IN (...) OR id IN (...)) </if>
      <if test="keyword != null and keyword != ''"> AND name LIKE #{kw} </if>
    </where>
    """
    conditions, params = [], {}
    if user_id is not None:
        conditions.append("""(
        owner_id = :user_id
        OR id IN (SELECT target_id FROM permissions WHERE user_id = :user_id AND target_type = 'kb' AND status = 'accept')
        OR id IN (
            SELECT d.kb_id FROM documents d JOIN permissions p ON p.target_type = 'doc' AND p.target_id = d.id
            WHERE p.user_id = :user_id AND p.status = 'accept'
        )
    )""")
        params["user_id"] = user_id
    if keyword and keyword.strip():
        conditions.append("name LIKE :kw ESCAPE '!'")
        params["kw"] = f"%{_escape_like(keyword.strip())}%"
    return (" WHERE " + " AND ".join(conditions)) if conditions else "", params


def count_accessible(conn: Connection, user_id: int | None, keyword: str | None) -> int:
    where, params = _build_where(user_id, keyword)
    return conn.execute(text(f"SELECT COUNT(*) FROM knowledge_bases{where}"), params).scalar_one()


def select_accessible_page(
    conn: Connection, user_id: int | None, keyword: str | None, offset: int, limit: int
) -> list[KnowledgeBase]:
    where, params = _build_where(user_id, keyword)
    sql = (
        f"SELECT {BASE_COLUMN_LIST} FROM knowledge_bases{where} "
        "ORDER BY updated_at DESC, id DESC LIMIT :limit OFFSET :offset"
    )
    rows = conn.execute(text(sql), {**params, "limit": limit, "offset": offset}).mappings().all()
    return [KnowledgeBase(**row) for row in rows]


# ==================== 新增 insert ====================

def insert(conn: Connection, name: str, owner_id: int) -> int:
    """插入知识库，返回自增主键 id。created_at / updated_at 使用数据库默认值。"""
    sql = "INSERT INTO knowledge_bases (name, owner_id) VALUES (:name, :owner_id)"
    return conn.execute(text(sql), {"name": name, "owner_id": owner_id}).lastrowid


# ==================== 修改 update ====================

def update_name(conn: Connection, id: int, name: str) -> int:
    sql = "UPDATE knowledge_bases SET name = :name, updated_at = CURRENT_TIMESTAMP WHERE id = :id"
    return conn.execute(text(sql), {"name": name, "id": id}).rowcount


def update_owner(conn: Connection, id: int, owner_id: int) -> int:
    sql = "UPDATE knowledge_bases SET owner_id = :owner_id, updated_at = CURRENT_TIMESTAMP WHERE id = :id"
    return conn.execute(text(sql), {"owner_id": owner_id, "id": id}).rowcount


def touch(conn: Connection, id: int) -> int:
    """知识库内的文档有增删改时刷新知识库的更新时间。"""
    sql = "UPDATE knowledge_bases SET updated_at = CURRENT_TIMESTAMP WHERE id = :id"
    return conn.execute(text(sql), {"id": id}).rowcount


# ==================== 删除 delete ====================

def delete_by_id(conn: Connection, id: int) -> int:
    sql = "DELETE FROM knowledge_bases WHERE id = :id"
    return conn.execute(text(sql), {"id": id}).rowcount
