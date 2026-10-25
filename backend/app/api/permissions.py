from fastapi import APIRouter, Depends
from sqlalchemy import Connection

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.permission import PermissionStatus, TargetType
from app.models.user import User
from app.schemas.permission import ApplicationIn, InvitationIn, PermissionOut, PermissionTypeIn
from app.schemas.user import Resp
from app.services import permission_service as perm_service

router = APIRouter(prefix="/permissions", tags=["权限管理"])


def _one(db: Connection, p) -> Resp[PermissionOut]:
    return Resp(data=perm_service.to_out(db, [p])[0])


@router.post("/invitations", response_model=Resp[PermissionOut], summary="邀请用户（需要对象的管理权限，被邀请人接受后生效）")
def invite(body: InvitationIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    return _one(db, perm_service.invite(db, user, body))


@router.post("/applications", response_model=Resp[PermissionOut], summary="申请权限（管理者同意后生效）")
def apply(body: ApplicationIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    return _one(db, perm_service.apply(db, user, body))


@router.get("/mine", response_model=Resp[list[PermissionOut]], summary="与我有关的授权（收到的邀请、我的申请、已获得的权限）")
def list_mine(
    status: PermissionStatus | None = None, user: User = Depends(get_current_user), db: Connection = Depends(get_db)
):
    return Resp(data=perm_service.to_out(db, perm_service.list_mine(db, user, status)))


@router.get("/approvals", response_model=Resp[list[PermissionOut]], summary="待我审批的申请")
def list_approvals(user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    return Resp(data=perm_service.to_out(db, perm_service.list_approvals(db, user)))


@router.get("", response_model=Resp[list[PermissionOut]], summary="某个知识库 / 文档的授权列表（需要管理权限）")
def list_by_target(
    target_type: TargetType, target_id: int, user: User = Depends(get_current_user), db: Connection = Depends(get_db)
):
    return Resp(data=perm_service.to_out(db, perm_service.list_by_target(db, user, target_type, target_id)))


@router.post("/{permission_id}/accept", response_model=Resp[PermissionOut], summary="接受邀请 / 同意申请")
def accept(permission_id: int, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    return _one(db, perm_service.accept(db, user, permission_id))


@router.post("/{permission_id}/reject", response_model=Resp[PermissionOut], summary="拒绝邀请 / 驳回申请")
def reject(permission_id: int, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    return _one(db, perm_service.reject(db, user, permission_id))


@router.patch("/{permission_id}", response_model=Resp[PermissionOut], summary="修改权限类型（需要管理权限）")
def change_type(
    permission_id: int, body: PermissionTypeIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)
):
    return _one(db, perm_service.change_type(db, user, permission_id, body.permission_type))


@router.delete("/{permission_id}", response_model=Resp[None], summary="撤销权限 / 放弃权限 / 撤回请求")
def remove(permission_id: int, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    perm_service.remove(db, user, permission_id)
    return Resp(message="已删除")
