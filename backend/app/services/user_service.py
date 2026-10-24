import uuid
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.exceptions import BizError, ErrorCode
from app.core.security import DUMMY_HASH, hash_password, verify_password
from app.mapper import user_mapper
from app.models.user import User, UserStatus
from app.schemas.user import PasswordChangeIn, ProfileUpdateIn, RegisterIn

AVATAR_URL_PREFIX = "/uploads/avatars/"
AVATAR_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}


def get_or_404(conn: Connection, user_id: int) -> User:
    user = user_mapper.select_by_id(conn, user_id)
    if not user:
        raise BizError("用户不存在", ErrorCode.NOT_FOUND, 404)
    return user


@contextmanager
def _unique_guard(conn: Connection):
    """把唯一索引冲突（并发注册等）转换为业务异常并回滚。"""
    try:
        yield
    except IntegrityError as e:
        conn.rollback()
        msg = str(e.orig).lower()
        if "email" in msg:
            raise BizError("邮箱已被注册", ErrorCode.EMAIL_EXISTS) from e
        raise BizError("用户名已存在", ErrorCode.USERNAME_EXISTS) from e


def register(conn: Connection, data: RegisterIn) -> User:
    if user_mapper.select_by_username(conn, data.username):
        raise BizError("用户名已存在", ErrorCode.USERNAME_EXISTS)
    if user_mapper.select_by_email(conn, data.email):
        raise BizError("邮箱已被注册", ErrorCode.EMAIL_EXISTS)

    with _unique_guard(conn):
        user_id = user_mapper.insert(
            conn,
            username=data.username,
            password=hash_password(data.password),
            email=data.email,
            nickname=data.nickname or data.username,
        )
        conn.commit()
    return user_mapper.select_by_id(conn, user_id)


def authenticate(conn: Connection, account: str, password: str) -> User:
    account = account.strip()
    if "@" in account:
        user = user_mapper.select_by_email(conn, account.lower())
    else:
        user = user_mapper.select_by_username(conn, account)

    if not user:
        verify_password(password, DUMMY_HASH)
        raise BizError("账号或密码错误", ErrorCode.LOGIN_FAILED, 401)
    if not verify_password(password, user.password):
        raise BizError("账号或密码错误", ErrorCode.LOGIN_FAILED, 401)
    ensure_active(user)
    return user


def ensure_active(user: User) -> None:
    if user.status == UserStatus.DISABLED:
        raise BizError("账号已被禁用，请联系管理员", ErrorCode.USER_DISABLED, 403)
    if user.status == UserStatus.DEACTIVATED:
        raise BizError("账号已注销", ErrorCode.USER_DISABLED, 403)


def update_profile(conn: Connection, user: User, data: ProfileUpdateIn) -> User:
    email = data.email if data.email and data.email != user.email else None
    if email and user_mapper.select_by_email(conn, email):
        raise BizError("邮箱已被注册", ErrorCode.EMAIL_EXISTS)
    nickname = data.nickname.strip() if data.nickname else None

    with _unique_guard(conn):
        user_mapper.update_selective(conn, user.id, nickname=nickname, email=email)
        conn.commit()
    return user_mapper.select_by_id(conn, user.id)


def change_password(conn: Connection, user: User, data: PasswordChangeIn) -> None:
    if not verify_password(data.old_password, user.password):
        raise BizError("原密码错误", ErrorCode.OLD_PASSWORD_WRONG)
    user_mapper.update_password(conn, user.id, hash_password(data.new_password))
    conn.commit()


def avatar_dir() -> Path:
    path = settings.UPLOAD_DIR / "avatars"
    path.mkdir(parents=True, exist_ok=True)
    return path


def update_avatar(conn: Connection, user: User, content: bytes, content_type: str | None) -> User:
    ext = AVATAR_TYPES.get(content_type or "")
    if not ext:
        raise BizError("仅支持 png / jpg / gif / webp 格式的图片", ErrorCode.FILE_INVALID)
    if len(content) > settings.AVATAR_MAX_BYTES:
        raise BizError(f"头像不能超过 {settings.AVATAR_MAX_BYTES // 1024 // 1024}MB", ErrorCode.FILE_INVALID)

    filename = f"{user.id}_{uuid.uuid4().hex}.{ext}"
    (avatar_dir() / filename).write_bytes(content)

    user_mapper.update_avatar(conn, user.id, AVATAR_URL_PREFIX + filename)
    conn.commit()

    # 删除旧的本地头像文件（外链头像不处理）
    if user.avatar and user.avatar.startswith(AVATAR_URL_PREFIX):
        (avatar_dir() / Path(user.avatar).name).unlink(missing_ok=True)
    return user_mapper.select_by_id(conn, user.id)


# ---------- 管理员 ----------

def list_users(
    conn: Connection, page: int, size: int, keyword: str | None, status: int | None, is_vip: bool | None
) -> tuple[list[User], int]:
    total = user_mapper.count_by_condition(conn, keyword, status, is_vip)
    items = user_mapper.select_page(conn, keyword, status, is_vip, offset=(page - 1) * size, limit=size) if total else []
    return items, total


def set_status(conn: Connection, operator: User, user_id: int, status: UserStatus) -> User:
    user = get_or_404(conn, user_id)
    if user.id == operator.id:
        raise BizError("不能修改自己的状态", ErrorCode.FORBIDDEN, 403)
    user_mapper.update_status(conn, user_id, status)
    conn.commit()
    return user_mapper.select_by_id(conn, user_id)


def set_vip(conn: Connection, user_id: int, is_vip: bool) -> User:
    get_or_404(conn, user_id)
    user_mapper.update_vip(conn, user_id, is_vip)
    conn.commit()
    return user_mapper.select_by_id(conn, user_id)
