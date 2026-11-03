"""
InviteLinkMapper —— invite_links 表的全部 SQL，写法与其他 mapper 一致。
"""
from datetime import datetime

from sqlalchemy import Connection, RowMapping, text

from app.models.invite_link import InviteLink
from app.models.permission import PermissionType, TargetType

BASE_COLUMN_LIST = "id, token, target_type, target_id, permission_type, creator_id, expires_at, max_uses, use_count, created_at"


def _row_to_link(row: RowMapping | None) -> InviteLink | None:
    return InviteLink(**row) if row else None


# ==================== 查询 select ====================

def select_by_id(conn: Connection, id: int) -> InviteLink | None:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM invite_links WHERE id = :id"
    return _row_to_link(conn.execute(text(sql), {"id": id}).mappings().first())


def select_by_token(conn: Connection, token: str) -> InviteLink | None:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM invite_links WHERE token = :token"
    return _row_to_link(conn.execute(text(sql), {"token": token}).mappings().first())


def select_by_target(conn: Connection, target_type: TargetType, target_id: int) -> list[InviteLink]:
    sql = f"""
        SELECT {BASE_COLUMN_LIST} FROM invite_links
        WHERE target_type = :target_type AND target_id = :target_id
        ORDER BY id DESC
    """
    rows = conn.execute(text(sql), {"target_type": target_type.value, "target_id": target_id}).mappings().all()
    return [InviteLink(**row) for row in rows]


# ==================== 新增 insert ====================

def insert(
    conn: Connection, token: str, target_type: TargetType, target_id: int, permission_type: PermissionType,
    creator_id: int, expires_at: datetime | None, max_uses: int | None,
) -> int:
    """新增一条邀请链接，返回自增主键 id。"""
    sql = """
        INSERT INTO invite_links (token, target_type, target_id, permission_type, creator_id, expires_at, max_uses)
        VALUES (:token, :target_type, :target_id, :permission_type, :creator_id, :expires_at, :max_uses)
    """
    params = {
        "token": token, "target_type": target_type.value, "target_id": target_id,
        "permission_type": permission_type.value, "creator_id": creator_id, "max_uses": max_uses,
        # 以字符串传入：MySQL / SQLite 通用（Python 3.12 起 sqlite3 默认的 datetime 适配器已弃用）
        "expires_at": expires_at.isoformat(sep=" ") if expires_at else None,
    }
    return conn.execute(text(sql), params).lastrowid


# ==================== 修改 update ====================

def increment_use(conn: Connection, id: int) -> int:
    """使用次数 +1；WHERE 中带上次数上限条件，防止并发超用。"""
    sql = """
        UPDATE invite_links SET use_count = use_count + 1
        WHERE id = :id AND (max_uses IS NULL OR use_count < max_uses)
    """
    return conn.execute(text(sql), {"id": id}).rowcount


# ==================== 删除 delete ====================

def delete_by_id(conn: Connection, id: int) -> int:
    return conn.execute(text("DELETE FROM invite_links WHERE id = :id"), {"id": id}).rowcount


def delete_by_target(conn: Connection, target_type: TargetType, target_id: int) -> int:
    sql = "DELETE FROM invite_links WHERE target_type = :target_type AND target_id = :target_id"
    return conn.execute(text(sql), {"target_type": target_type.value, "target_id": target_id}).rowcount


def delete_by_kb(conn: Connection, kb_id: int) -> int:
    """删除知识库时清理：知识库本身的链接 + 其下所有文档的链接（须在删除文档之前执行）。"""
    sql = """
        DELETE FROM invite_links
        WHERE (target_type = 'kb' AND target_id = :kb_id)
           OR (target_type = 'doc' AND target_id IN (SELECT id FROM documents WHERE kb_id = :kb_id))
    """
    return conn.execute(text(sql), {"kb_id": kb_id}).rowcount
