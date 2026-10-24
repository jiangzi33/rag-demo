from fastapi import APIRouter, Depends
from sqlalchemy import Connection

from app.core.security import create_access_token
from app.db.session import get_db
from app.schemas.user import LoginIn, RegisterIn, Resp, TokenOut, UserOut
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["认证"])


def _token_resp(user) -> Resp[TokenOut]:
    token, expires_in = create_access_token(user.id)
    return Resp(data=TokenOut(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user)))


@router.post("/register", response_model=Resp[TokenOut], summary="注册（成功后直接登录）")
def register(body: RegisterIn, db: Connection = Depends(get_db)):
    user = user_service.register(db, body)
    return _token_resp(user)


@router.post("/login", response_model=Resp[TokenOut], summary="登录（支持用户名或邮箱）")
def login(body: LoginIn, db: Connection = Depends(get_db)):
    user = user_service.authenticate(db, body.account, body.password)
    return _token_resp(user)
