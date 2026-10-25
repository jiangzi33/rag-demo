from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.user import UserBriefOut


def _strip_name(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("知识库名称不能为空")
    return v


# ---------- 请求体 ----------

class KnowledgeBaseCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)

    _name = field_validator("name")(_strip_name)


class KnowledgeBaseUpdateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)

    _name = field_validator("name")(_strip_name)


class OwnerTransferIn(BaseModel):
    owner_id: int = Field(ge=1, description="新管理员的用户 id")


# ---------- 响应体 ----------

class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    owner_id: int
    owner: UserBriefOut | None = None  # 管理员信息，用户被物理删除时为 None
    # 当前用户对知识库的权限：owner / manage / write / read；没有知识库级权限时为 None（可能仍对其中个别文档有权限）
    my_permission: str | None = None
    pending_permission: str | None = None  # 当前用户对知识库待审批的申请：read / write / manage
    created_at: datetime
    updated_at: datetime
