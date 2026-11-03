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


# ---------- 邀请链接 ----------

class InviteLinkIn(BaseModel):
    """管理者生成邀请链接：任何登录用户打开链接即直接获得该权限"""
    target_type: TargetType
    target_id: int = Field(ge=1)
    permission_type: PermissionType
    expires_in_days: int | None = Field(default=7, ge=1, le=365, description="有效天数，为空表示永久有效")
    max_uses: int | None = Field(default=None, ge=1, le=10000, description="可使用次数，为空表示不限")


class InviteLinkOut(BaseModel):
    id: int
    token: str  # 前端拼成 /#/invite/{token}
    target_type: TargetType
    target_id: int
    permission_type: PermissionType
    creator_id: int
    creator: UserBriefOut | None = None
    expires_at: datetime | None
    max_uses: int | None
    use_count: int
    valid: bool  # 未过期且未用完
    created_at: datetime


class InviteLinkRedeemOut(BaseModel):
    """打开邀请链接的结果，前端据此跳转到目标对象"""
    target_type: TargetType
    target_id: int
    target_name: str
    kb_id: int
    permission_type: PermissionType  # 链接授予的权限
    granted: bool  # 本次是否新获得 / 提升了权限；已拥有同等或更高权限时为 False
    my_permission: str  # 打开链接后的有效权限：owner / manage / write / read
