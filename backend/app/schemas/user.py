import re
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator

from app.core.config import settings
from app.models.user import UserStatus

T = TypeVar("T")

USERNAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{3,31}$"


def _check_password_strength(v: str) -> str:
    if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
        raise ValueError("密码需同时包含字母和数字")
    if len(v.encode("utf-8")) > 72:  # bcrypt 的长度上限
        raise ValueError("密码过长")
    return v


class Resp(BaseModel, Generic[T]):
    """统一响应结构"""
    code: int = 0
    message: str = "ok"
    data: T | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int


# ---------- 请求体 ----------

class RegisterIn(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN, description="4-32位，字母开头，仅含字母/数字/下划线")
    password: str = Field(min_length=6, max_length=64)
    email: EmailStr
    nickname: str | None = Field(default=None, max_length=32, description="不填则默认使用用户名")

    _pwd = field_validator("password")(_check_password_strength)

    @field_validator("email")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return v.lower()

    @field_validator("nickname")
    @classmethod
    def _strip_nickname(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None


class LoginIn(BaseModel):
    account: str = Field(min_length=1, max_length=128, description="用户名或邮箱")
    password: str = Field(min_length=1, max_length=64)


class ProfileUpdateIn(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=32)
    email: EmailStr | None = None

    @field_validator("email")
    @classmethod
    def _lower_email(cls, v: str | None) -> str | None:
        return v.lower() if v else v


class PasswordChangeIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=6, max_length=64)

    _pwd = field_validator("new_password")(_check_password_strength)


class StatusUpdateIn(BaseModel):
    status: UserStatus


class VipUpdateIn(BaseModel):
    is_vip: bool


# ---------- 响应体 ----------

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    nickname: str
    avatar: str | None
    created_at: datetime
    is_vip: bool
    status: UserStatus

    @computed_field
    @property
    def is_admin(self) -> bool:
        return self.username in settings.admin_usernames


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
