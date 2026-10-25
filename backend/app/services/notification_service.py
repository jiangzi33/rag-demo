"""
站内通知：别人变更了我的权限时通知我（邀请、审批结果、调整、撤销、知识库转让）。

notify 只写入不提交，由调用方在同一事务中与权限变更一起提交，保证“有变更就有通知”。
自己操作自己的记录（如放弃权限、撤回申请）不发通知。
"""
from dataclasses import asdict

from sqlalchemy import Connection

from app.core.exceptions import BizError, ErrorCode
from app.mapper import notification_mapper, user_mapper
from app.models.notification import Notification, NotificationType
from app.models.user import User
from app.schemas.notification import NotificationOut, NotificationPage
from app.schemas.user import UserBriefOut


def notify(
    conn: Connection, user_id: int, actor: User, type: NotificationType, content: str,
    target_type: str | None = None, target_id: int | None = None, kb_id: int | None = None,
) -> None:
    if user_id == actor.id:
        return
    notification_mapper.insert(conn, user_id, actor.id, type, content, target_type, target_id, kb_id)


def _to_out(conn: Connection, items: list[Notification]) -> list[NotificationOut]:
    actors = {u.id: UserBriefOut.model_validate(u) for u in user_mapper.select_by_ids(conn, list({n.actor_id for n in items}))}
    return [NotificationOut(**asdict(n), actor=actors.get(n.actor_id)) for n in items]


def list_mine(conn: Connection, user: User, page: int, size: int, unread_only: bool) -> NotificationPage:
    total = notification_mapper.count_by_user(conn, user.id, unread_only)
    unread = total if unread_only else notification_mapper.count_by_user(conn, user.id, unread_only=True)
    items = notification_mapper.select_page_by_user(conn, user.id, unread_only, (page - 1) * size, size) if total else []
    return NotificationPage(items=_to_out(conn, items), total=total, page=page, size=size, unread=unread)


def mark_read(conn: Connection, user: User, notification_id: int) -> None:
    if not notification_mapper.mark_read(conn, notification_id, user.id):
        raise BizError("通知不存在", ErrorCode.NOT_FOUND, 404)
    conn.commit()


def mark_all_read(conn: Connection, user: User) -> int:
    count = notification_mapper.mark_all_read(conn, user.id)
    conn.commit()
    return count
