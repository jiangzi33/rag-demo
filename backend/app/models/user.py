from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum


class UserStatus(IntEnum):
    DISABLED = 0  # 禁用
    NORMAL = 1  # 正常
    DEACTIVATED = 2  # 已注销（逻辑删除）


@dataclass
class User:
    """用户实体，对应 users 表的一行（相当于 Java 中的 POJO / Entity）。"""

    id: int
    username: str
    password: str
    email: str
    nickname: str
    avatar: str | None
    created_at: datetime
    is_vip: bool
    status: int

    def __post_init__(self):
        # 相当于 MyBatis 的 TypeHandler：MySQL TINYINT -> bool；SQLite 中时间以字符串返回
        self.is_vip = bool(self.is_vip)
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
