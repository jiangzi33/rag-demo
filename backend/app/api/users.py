from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import Connection

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.exceptions import BizError, ErrorCode
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import PasswordChangeIn, ProfileUpdateIn, Resp, UserOut
from app.services import user_service

router = APIRouter(prefix="/users", tags=["个人中心"])


@router.get("/me", response_model=Resp[UserOut], summary="当前用户信息")
def me(user: User = Depends(get_current_user)):
    return Resp(data=UserOut.model_validate(user))


@router.put("/me", response_model=Resp[UserOut], summary="修改昵称 / 邮箱")
def update_me(body: ProfileUpdateIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    user = user_service.update_profile(db, user, body)
    return Resp(data=UserOut.model_validate(user))


@router.put("/me/password", response_model=Resp[None], summary="修改密码")
def change_password(body: PasswordChangeIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    user_service.change_password(db, user, body)
    return Resp(message="密码修改成功")


@router.post("/me/avatar", response_model=Resp[UserOut], summary="上传头像")
async def upload_avatar(
    file: UploadFile = File(...), user: User = Depends(get_current_user), db: Connection = Depends(get_db)
):
    # 多读 1 字节用于判断是否超限，避免把超大文件整个读进内存
    content = await file.read(settings.AVATAR_MAX_BYTES + 1)
    if not content:
        raise BizError("文件为空", ErrorCode.FILE_INVALID)
    user = user_service.update_avatar(db, user, content, file.content_type)
    return Resp(data=UserOut.model_validate(user))
