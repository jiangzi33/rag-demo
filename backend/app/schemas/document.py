from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.html_sanitizer import clean_html
from app.schemas.user import UserBriefOut

# 正文为富文本 HTML，图片以 URL 引用（不内嵌 base64），按字符数限制，远小于 MEDIUMTEXT 的 16MB 上限
CONTENT_MAX_LENGTH = 2_000_000


def _strip_title(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("文档标题不能为空")
    return v


# ---------- 请求体 ----------

class DocumentIn(BaseModel):
    """创建和编辑共用：标题 + 正文（富文本 HTML）"""
    title: str = Field(min_length=1, max_length=128)
    content: str = Field(default="", max_length=CONTENT_MAX_LENGTH)

    _title = field_validator("title")(_strip_title)

    @field_validator("content")
    @classmethod
    def _clean_content(cls, v: str) -> str:
        return clean_html(v)


# ---------- 响应体 ----------

class DocumentBriefOut(BaseModel):
    """列表项，不含正文"""
    id: int
    kb_id: int
    title: str
    author_id: int
    author: UserBriefOut | None = None  # 作者被物理删除时为 None
    my_permission: str | None = None  # 当前用户对文档的有效权限：owner / manage / write / read
    pending_permission: str | None = None  # 当前用户对该文档待审批的申请：read / write / manage，审批期间 my_permission 不变
    published_at: datetime
    updated_at: datetime


class ImageOut(BaseModel):
    url: str


class DocumentOut(DocumentBriefOut):
    """详情，含正文"""
    content: str
