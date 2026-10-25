from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.permission import PermissionStatus, PermissionType, TargetType
from app.schemas.user import UserBriefOut


# ---------- 请求体 ----------

class InvitationIn(BaseModel):
    """管理者邀请用户：被邀请人接受后生效"""
    username: str = Field(min_length=1, max_length=32, description="被邀请用户的用户名")
    target_type: TargetType
    target_id: int = Field(ge=1)
    permission_type: PermissionType


class ApplicationIn(BaseModel):
    """用户申请权限：管理者同意后生效"""
    target_type: TargetType
    target_id: int = Field(ge=1)
    permission_type: PermissionType


class PermissionTypeIn(BaseModel):
    permission_type: PermissionType


# ---------- 响应体 ----------

class PermissionOut(BaseModel):
    id: int
    permission_type: PermissionType
    user_id: int
    user: UserBriefOut | None = None  # 被授权用户
    target_type: TargetType
    target_id: int
    target_name: str | None = None  # 知识库名称 / 文档标题，对象已删除时为 None
    kb_id: int | None = None  # 对象所在的知识库（知识库授权即其本身），前端拼跳转链接用
    status: PermissionStatus
    initiator_id: int
    initiator: UserBriefOut | None = None
    direction: Literal["invite", "apply"]  # invite-管理者邀请 apply-用户申请
    created_at: datetime
    granted_at: datetime | None
