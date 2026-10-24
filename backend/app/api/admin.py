from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from app.api.deps import get_current_admin
from app.db.session import get_db
from app.models.user import User, UserStatus
from app.schemas.user import Page, Resp, StatusUpdateIn, UserOut, VipUpdateIn
from app.services import user_service

router = APIRouter(prefix="/admin/users", tags=["用户管理（管理员）"])


@router.get("", response_model=Resp[Page[UserOut]], summary="用户列表")
def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64, description="匹配用户名/昵称/邮箱"),
    status: UserStatus | None = None,
    is_vip: bool | None = None,
    _: User = Depends(get_current_admin),
    db: Connection = Depends(get_db),
):
    items, total = user_service.list_users(db, page, size, keyword, status, is_vip)
    return Resp(data=Page(items=[UserOut.model_validate(u) for u in items], total=total, page=page, size=size))


@router.patch("/{user_id}/status", response_model=Resp[UserOut], summary="修改用户状态（启用/禁用/注销）")
def update_status(
    user_id: int, body: StatusUpdateIn, admin: User = Depends(get_current_admin), db: Connection = Depends(get_db)
):
    user = user_service.set_status(db, admin, user_id, body.status)
    return Resp(data=UserOut.model_validate(user))


@router.patch("/{user_id}/vip", response_model=Resp[UserOut], summary="设置 / 取消 VIP")
def update_vip(
    user_id: int, body: VipUpdateIn, _: User = Depends(get_current_admin), db: Connection = Depends(get_db)
):
    user = user_service.set_vip(db, user_id, body.is_vip)
    return Resp(data=UserOut.model_validate(user))
