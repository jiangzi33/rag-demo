from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class NotificationType(StrEnum):
    PERM_INVITED = "perm_invited"  # 收到权限邀请
    PERM_APPROVED = "perm_approved"  # 申请被同意
    PERM_REJECTED = "perm_rejected"  # 申请被驳回
    PERM_CHANGED = "perm_changed"  # 权限 / 待处理请求的类型被调整
    PERM_REVOKED = "perm_revoked"  # 权限被撤销
    PERM_WITHDRAWN = "perm_withdrawn"  # 待处理的邀请被撤回 / 申请被删除
    KB_TRANSFERRED = "kb_transferred"  # 知识库被转让给我
    LINK_JOINED = "link_joined"  # 有人通过我生成的邀请链接获得了权限


@dataclass
class Notification:
    """站内通知实体，对应 notifications 表的一行。

    content 在发生时生成并保存（不含操作人），对象之后被改名 / 删除也不影响历史通知的展示。
    """

    id: int
    user_id: int  # 接收人
    actor_id: int  # 操作人
    type: NotificationType
    content: str
    target_type: str | None  # kb / doc，前端拼跳转链接用
    target_id: int | None
    kb_id: int | None  # 对象所在知识库
    is_read: bool
    created_at: datetime

    def __post_init__(self):
        self.type = NotificationType(self.type)
        self.is_read = bool(self.is_read)
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
