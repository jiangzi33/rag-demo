from dataclasses import dataclass
from datetime import datetime

from app.models.permission import PermissionType, TargetType


@dataclass
class InviteLink:
    """邀请链接实体，对应 invite_links 表的一行。

    管理者生成链接后分享出去，任何登录用户打开链接即直接获得对应权限（无需再审批），并跳转到目标对象。
    expires_at / max_uses 为 None 表示永久有效 / 不限次数；撤销链接即删除记录。
    """

    id: int
    token: str
    target_type: TargetType
    target_id: int
    permission_type: PermissionType
    creator_id: int
    expires_at: datetime | None
    max_uses: int | None
    use_count: int
    created_at: datetime

    def __post_init__(self):
        self.target_type = TargetType(self.target_type)
        self.permission_type = PermissionType(self.permission_type)
        # SQLite 中时间以字符串返回
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        if isinstance(self.expires_at, str):
            self.expires_at = datetime.fromisoformat(self.expires_at)

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now()

    @property
    def is_exhausted(self) -> bool:
        return self.max_uses is not None and self.use_count >= self.max_uses
