from datetime import datetime

from pydantic import BaseModel

from app.models.notification import NotificationType
from app.schemas.user import Page, UserBriefOut


class NotificationOut(BaseModel):
    id: int
    type: NotificationType
    actor_id: int
    actor: UserBriefOut | None = None  # 操作人，用户被物理删除时为 None
    content: str  # 不含操作人，前端展示为“{actor} {content}”
    target_type: str | None
    target_id: int | None
    kb_id: int | None
    is_read: bool
    created_at: datetime


class NotificationPage(Page[NotificationOut]):
    unread: int  # 未读总数，顶部铃铛角标用
