from contextlib import contextmanager

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.exceptions import BizError, ErrorCode
from app.mapper import document_mapper
from app.mapper import knowledge_base_mapper as kb_mapper
from app.mapper import user_mapper
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User, UserStatus
from app.schemas.knowledge_base import KnowledgeBaseOut
from app.schemas.user import UserBriefOut


def to_out(conn: Connection, kbs: list[KnowledgeBase]) -> list[KnowledgeBaseOut]:
    """组装响应：一次批量查询补齐管理员信息，避免 N+1。"""
    owners = {u.id: u for u in user_mapper.select_by_ids(conn, list({kb.owner_id for kb in kbs}))}
    return [
        KnowledgeBaseOut.model_validate(kb).model_copy(
            update={"owner": UserBriefOut.model_validate(owners[kb.owner_id]) if kb.owner_id in owners else None}
        )
        for kb in kbs
    ]


def _is_manager(user: User, kb: KnowledgeBase) -> bool:
    """知识库管理员或系统管理员可以管理该知识库。"""
    return kb.owner_id == user.id or user.username in settings.admin_usernames


def get_managed_or_404(conn: Connection, user: User, kb_id: int) -> KnowledgeBase:
    kb = kb_mapper.select_by_id(conn, kb_id)
    # 无权限时同样返回 404，不暴露他人知识库是否存在
    if not kb or not _is_manager(user, kb):
        raise BizError("知识库不存在", ErrorCode.NOT_FOUND, 404)
    return kb


def _ensure_name_free(conn: Connection, owner_id: int, name: str, exclude_id: int | None = None) -> None:
    exists = kb_mapper.select_by_owner_and_name(conn, owner_id, name)
    if exists and exists.id != exclude_id:
        raise BizError("已存在同名知识库", ErrorCode.KB_NAME_EXISTS)


@contextmanager
def _unique_guard(conn: Connection):
    """并发下 uk_owner_name 冲突转换为业务异常。"""
    try:
        yield
    except IntegrityError as e:
        conn.rollback()
        raise BizError("已存在同名知识库", ErrorCode.KB_NAME_EXISTS) from e


def create(conn: Connection, user: User, name: str) -> KnowledgeBase:
    _ensure_name_free(conn, user.id, name)
    with _unique_guard(conn):
        kb_id = kb_mapper.insert(conn, name=name, owner_id=user.id)
        conn.commit()
    return kb_mapper.select_by_id(conn, kb_id)


def list_mine(
    conn: Connection, user: User, page: int, size: int, keyword: str | None
) -> tuple[list[KnowledgeBase], int]:
    total = kb_mapper.count_by_condition(conn, user.id, keyword)
    items = kb_mapper.select_page(conn, user.id, keyword, offset=(page - 1) * size, limit=size) if total else []
    return items, total


def rename(conn: Connection, user: User, kb_id: int, name: str) -> KnowledgeBase:
    kb = get_managed_or_404(conn, user, kb_id)
    if name == kb.name:
        return kb
    _ensure_name_free(conn, kb.owner_id, name, exclude_id=kb.id)
    with _unique_guard(conn):
        kb_mapper.update_name(conn, kb.id, name)
        conn.commit()
    return kb_mapper.select_by_id(conn, kb.id)


def transfer_owner(conn: Connection, user: User, kb_id: int, owner_id: int) -> KnowledgeBase:
    kb = get_managed_or_404(conn, user, kb_id)
    if owner_id == kb.owner_id:
        return kb
    new_owner = user_mapper.select_by_id(conn, owner_id)
    if not new_owner or new_owner.status != UserStatus.NORMAL:
        raise BizError("目标用户不存在或不可用", ErrorCode.NOT_FOUND, 404)
    _ensure_name_free(conn, owner_id, kb.name)
    with _unique_guard(conn):
        kb_mapper.update_owner(conn, kb.id, owner_id)
        conn.commit()
    return kb_mapper.select_by_id(conn, kb.id)


def delete(conn: Connection, user: User, kb_id: int) -> None:
    kb = get_managed_or_404(conn, user, kb_id)
    # 同一事务内先删文档再删知识库，避免留下孤儿文档
    document_mapper.delete_by_kb_id(conn, kb.id)
    kb_mapper.delete_by_id(conn, kb.id)
    conn.commit()
    # 数据库提交成功后再删除该知识库的正文图片
    from app.services.document_service import remove_kb_images  # 避免循环导入

    remove_kb_images(kb.id)
