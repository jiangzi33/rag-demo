from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PermissionType(StrEnum):
    READ = "read"  # 读：查看知识库 / 文档
    WRITE = "write"  # 写：新建、编辑文档
    MANAGE = "manage"  # 管理：在写的基础上删除文档、管理授权、修改知识库名称


class TargetType(StrEnum):
    KB = "kb"  # 知识库（授权覆盖其中所有文档）
    DOC = "doc"  # 单篇文档


class PermissionStatus(StrEnum):
    PENDING = "pending"  # 待处理
    ACCEPT = "accept"  # 已接受（生效）
    REJECT = "reject"  # 已拒绝


@dataclass
class Permission:
    """权限实体，对应 permissions 表的一行。

    initiator_id == user_id 表示用户自己申请，由目标的管理者审批；
    否则表示管理者邀请，由被授权用户接受或拒绝。
    """

    id: int
    permission_type: PermissionType
    user_id: int
    target_type: TargetType
    target_id: int
    status: PermissionStatus
    initiator_id: int
    created_at: datetime
    granted_at: datetime | None

    def __post_init__(self):
        self.permission_type = PermissionType(self.permission_type)
        self.target_type = TargetType(self.target_type)
        self.status = PermissionStatus(self.status)
        # SQLite 中时间以字符串返回
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        if isinstance(self.granted_at, str):
            self.granted_at = datetime.fromisoformat(self.granted_at)

    @property
    def is_application(self) -> bool:
        return self.initiator_id == self.user_id
