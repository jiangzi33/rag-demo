from contextlib import contextmanager

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import BizError, ErrorCode
from app.mapper import document_chunk_mapper as chunk_mapper
from app.mapper import document_mapper
from app.mapper import knowledge_base_mapper as kb_mapper
from app.mapper import invite_link_mapper, permission_mapper, user_mapper
from app.models.knowledge_base import KnowledgeBase
from app.models.notification import NotificationType
from app.models.permission import TargetType
from app.models.user import User, UserStatus
from app.services import access_service as access
from app.services import notification_service
from app.services.access_service import Level
from app.schemas.knowledge_base import KnowledgeBaseOut
from app.schemas.user import UserBriefOut


def to_out(conn: Connection, user: User, kbs: list[KnowledgeBase]) -> list[KnowledgeBaseOut]:
    """组装响应：批量补齐管理员信息和当前用户的权限，避免 N+1。"""
    owners = {u.id: u for u in user_mapper.select_by_ids(conn, list({kb.owner_id for kb in kbs}))}
    kb_ids = [kb.id for kb in kbs]
    granted = permission_mapper.select_accepted_types(conn, user.id, TargetType.KB, kb_ids)
    pending = permission_mapper.select_pending_application_types(conn, user.id, TargetType.KB, kb_ids)

    def my_level(kb: KnowledgeBase) -> Level:
        if kb.owner_id == user.id or access.is_system_admin(user):
            return Level.OWNER
        return access.LEVEL_OF[granted[kb.id]] if kb.id in granted else Level.NONE

    return [
        KnowledgeBaseOut.model_validate(kb).model_copy(update={
            "owner": UserBriefOut.model_validate(owners[kb.owner_id]) if kb.owner_id in owners else None,
            "my_permission": my_level(kb).label,
            "pending_permission": pending[kb.id].value if kb.id in pending else None,
        })
        for kb in kbs
    ]


def get_visible(conn: Connection, user: User, kb_id: int) -> KnowledgeBase:
    """查看知识库详情：所有登录用户可见。"""
    return access.require_kb(conn, user, kb_id, Level.NONE)[0]


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


def list_visible(
    conn: Connection, user: User, page: int, size: int, keyword: str | None, only_mine: bool = False
) -> tuple[list[KnowledgeBase], int]:
    """全部知识库；only_mine=True 时只列出我有权限的：自己拥有的 + 被授权的（含只被授权了其中部分文档的）。"""
    user_id = user.id if only_mine else None
    total = kb_mapper.count_accessible(conn, user_id, keyword)
    items = kb_mapper.select_accessible_page(conn, user_id, keyword, offset=(page - 1) * size, limit=size) if total else []
    return items, total


def rename(conn: Connection, user: User, kb_id: int, name: str) -> KnowledgeBase:
    kb, _ = access.require_kb(conn, user, kb_id, Level.MANAGE)
    if name == kb.name:
        return kb
    _ensure_name_free(conn, kb.owner_id, name, exclude_id=kb.id)
    with _unique_guard(conn):
        kb_mapper.update_name(conn, kb.id, name)
        conn.commit()
    return kb_mapper.select_by_id(conn, kb.id)


def transfer_owner(conn: Connection, user: User, kb_id: int, owner_id: int) -> KnowledgeBase:
    kb, _ = access.require_kb(conn, user, kb_id, Level.OWNER)
    if owner_id == kb.owner_id:
        return kb
    new_owner = user_mapper.select_by_id(conn, owner_id)
    if not new_owner or new_owner.status != UserStatus.NORMAL:
        raise BizError("目标用户不存在或不可用", ErrorCode.NOT_FOUND, 404)
    _ensure_name_free(conn, owner_id, kb.name)
    with _unique_guard(conn):
        kb_mapper.update_owner(conn, kb.id, owner_id)
        # 新拥有者原有的知识库授权已无意义，一并删除
        permission_mapper.delete_by_user_and_target(conn, owner_id, TargetType.KB, kb.id)
        notification_service.notify(
            conn, owner_id, user, NotificationType.KB_TRANSFERRED, f"将知识库「{kb.name}」转让给你，你现在是拥有者",
            TargetType.KB.value, kb.id, kb.id,
        )
        conn.commit()
    return kb_mapper.select_by_id(conn, kb.id)


def delete(conn: Connection, user: User, kb_id: int) -> None:
    kb, _ = access.require_kb(conn, user, kb_id, Level.OWNER)
    # 同一事务内依次删除授权和邀请链接、切分块、文档、知识库，避免留下孤儿数据（授权要在文档之前删，需要按文档反查知识库）
    permission_mapper.delete_by_kb(conn, kb.id)
    invite_link_mapper.delete_by_kb(conn, kb.id)
    chunk_mapper.delete_by_kb_id(conn, kb.id)
    document_mapper.delete_by_kb_id(conn, kb.id)
    kb_mapper.delete_by_id(conn, kb.id)
    conn.commit()
    # 数据库提交成功后再删除该知识库的正文图片
    from app.services.document_service import remove_kb_images  # 避免循环导入

    remove_kb_images(kb.id)
