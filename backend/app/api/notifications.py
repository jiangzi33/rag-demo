from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.notification import NotificationPage
from app.schemas.user import Resp
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["通知"])


@router.get("", response_model=Resp[NotificationPage], summary="我的通知（时间倒序，附带未读总数）")
def list_mine(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False, description="只看未读"),
    user: User = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    return Resp(data=notification_service.list_mine(db, user, page, size, unread_only))


@router.post("/read-all", response_model=Resp[None], summary="全部标记为已读")
def mark_all_read(user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    notification_service.mark_all_read(db, user)
    return Resp(message="已全部标记为已读")


@router.post("/{notification_id}/read", response_model=Resp[None], summary="标记为已读")
def mark_read(notification_id: int, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    notification_service.mark_read(db, user, notification_id)
    return Resp(message="已读")
