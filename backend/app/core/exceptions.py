from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class BizError(Exception):
    """业务异常：code 为业务错误码，http_status 为 HTTP 状态码。"""

    def __init__(self, message: str, code: int = 40000, http_status: int = 400):
        self.message = message
        self.code = code
        self.http_status = http_status


class ErrorCode:
    PARAM_INVALID = 40001
    USERNAME_EXISTS = 40010
    EMAIL_EXISTS = 40011
    LOGIN_FAILED = 40012
    OLD_PASSWORD_WRONG = 40013
    FILE_INVALID = 40020
    KB_NAME_EXISTS = 40030
    PERMISSION_CONFLICT = 40040
    INVITE_LINK_INVALID = 40041
    UNAUTHORIZED = 40100
    USER_DISABLED = 40300
    FORBIDDEN = 40301
    NOT_FOUND = 40400
    RAG_UNAVAILABLE = 50300  # 向量化服务 / 向量数据库不可用或配置不一致


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def biz_error_handler(_: Request, exc: BizError):
        return JSONResponse(status_code=exc.http_status, content={"code": exc.code, "message": exc.message, "data": None})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"] if p not in ("body", "query", "path"))
        msg = first["msg"].removeprefix("Value error, ")
        return JSONResponse(
            status_code=422,
            content={"code": ErrorCode.PARAM_INVALID, "message": f"{field}: {msg}" if field else msg, "data": None},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code * 100, "message": str(exc.detail), "data": None},
        )
