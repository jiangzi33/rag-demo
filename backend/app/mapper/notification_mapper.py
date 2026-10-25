"""
NotificationMapper —— notifications 表的全部 SQL，写法与其他 mapper 一致。
"""
from sqlalchemy import Connection, text

from app.models.notification import Notification, NotificationType

BASE_COLUMN_LIST = "id, user_id, actor_id, type, content, target_type, target_id, kb_id, is_read, created_at"


def _build_where(user_id: int, unread_only: bool) -> tuple[str, dict]:
    """
    <where>
      user_id = #{userId}
      <if test="unreadOnly"> AND is_read = 0 </if>
    </where>
    """
    where = " WHERE user_id = :user_id" + (" AND is_read = 0" if unread_only else "")
    return where, {"user_id": user_id}


# ==================== 查询 select ====================

def count_by_user(conn: Connection, user_id: int, unread_only: bool = False) -> int:
    where, params = _build_where(user_id, unread_only)
    return conn.execute(text(f"SELECT COUNT(*) FROM notifications{where}"), params).scalar_one()


def select_page_by_user(conn: Connection, user_id: int, unread_only: bool, offset: int, limit: int) -> list[Notification]:
    """按时间倒序（id 自增，与发生顺序一致）。"""
    where, params = _build_where(user_id, unread_only)
    sql = f"SELECT {BASE_COLUMN_LIST} FROM notifications{where} ORDER BY id DESC LIMIT :limit OFFSET :offset"
    rows = conn.execute(text(sql), {**params, "limit": limit, "offset": offset}).mappings().all()
    return [Notification(**row) for row in rows]


# ==================== 新增 insert ====================

def insert(
    conn: Connection, user_id: int, actor_id: int, type: NotificationType, content: str,
    target_type: str | None, target_id: int | None, kb_id: int | None,
) -> int:
    sql = """
        INSERT INTO notifications (user_id, actor_id, type, content, target_type, target_id, kb_id)
        VALUES (:user_id, :actor_id, :type, :content, :target_type, :target_id, :kb_id)
    """
    params = {
        "user_id": user_id, "actor_id": actor_id, "type": type.value, "content": content,
        "target_type": target_type, "target_id": target_id, "kb_id": kb_id,
    }
    return conn.execute(text(sql), params).lastrowid


# ==================== 修改 update ====================

def mark_read(conn: Connection, id: int, user_id: int) -> int:
    """WHERE 中带上 user_id，只能标记自己的通知。"""
    sql = "UPDATE notifications SET is_read = 1 WHERE id = :id AND user_id = :user_id"
    return conn.execute(text(sql), {"id": id, "user_id": user_id}).rowcount


def mark_all_read(conn: Connection, user_id: int) -> int:
    sql = "UPDATE notifications SET is_read = 1 WHERE user_id = :user_id AND is_read = 0"
    return conn.execute(text(sql), {"user_id": user_id}).rowcount
