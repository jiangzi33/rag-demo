from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Connection

from app.core.config import settings
from app.core.exceptions import BizError, ErrorCode
from app.core.security import decode_access_token
from app.db.session import get_db
from app.mapper import user_mapper
from app.models.user import User
from app.services import user_service

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Connection = Depends(get_db),
) -> User:
    if cred is None:
        raise BizError("请先登录", ErrorCode.UNAUTHORIZED, 401)
    user_id = decode_access_token(cred.credentials)
    user = user_mapper.select_by_id(db, user_id) if user_id else None
    if user is None:
        raise BizError("登录已失效，请重新登录", ErrorCode.UNAUTHORIZED, 401)
    # 每次请求都校验状态，被禁用的用户立即失去访问权限
    user_service.ensure_active(user)
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if user.username not in settings.admin_usernames:
        raise BizError("无管理员权限", ErrorCode.FORBIDDEN, 403)
    return user
