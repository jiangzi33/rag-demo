"""
统一的访问控制：计算用户对知识库 / 文档的有效权限等级。

等级从低到高：无 < 读 < 写 < 管理 < 拥有者
- 拥有者：知识库的管理员（knowledge_bases.owner_id）或系统管理员，拥有全部权限，包括删除知识库、转让
- 知识库授权覆盖其中所有文档；文档授权只对该篇文档生效；两者取较高者
- 所有登录用户都能看到全部知识库及其中的文档列表（标题、作者、时间），用于发现内容并申请权限：
  读 —— 打开文档；写 —— 新建 / 编辑文档；管理 —— 额外可删除文档、管理授权

对象不存在时返回 404；存在但权限不足时返回 403。
"""
from enum import IntEnum

from sqlalchemy import Connection

from app.core.config import settings
from app.core.exceptions import BizError, ErrorCode
from app.mapper import document_mapper
from app.mapper import knowledge_base_mapper as kb_mapper
from app.mapper import permission_mapper
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.permission import PermissionType, TargetType
from app.models.user import User


class Level(IntEnum):
    NONE = 0
    READ = 1
    WRITE = 2
    MANAGE = 3
    OWNER = 4

    @property
    def label(self) -> str | None:
        """返回给前端的 my_permission：owner / manage / write / read，无权限时为 None"""
        return None if self == Level.NONE else self.name.lower()


LEVEL_OF = {PermissionType.READ: Level.READ, PermissionType.WRITE: Level.WRITE, PermissionType.MANAGE: Level.MANAGE}


def is_system_admin(user: User) -> bool:
    return user.username in settings.admin_usernames


def kb_level(conn: Connection, user: User, kb: KnowledgeBase) -> Level:
    if kb.owner_id == user.id or is_system_admin(user):
        return Level.OWNER
    granted = permission_mapper.select_accepted_types(conn, user.id, TargetType.KB, [kb.id]).get(kb.id)
    return LEVEL_OF[granted] if granted else Level.NONE


def doc_level(conn: Connection, user: User, doc: Document, kb_lvl: Level) -> Level:
    if kb_lvl >= Level.MANAGE:
        return kb_lvl  # 已是最高的非拥有者权限，文档授权不会更高
    granted = permission_mapper.select_accepted_types(conn, user.id, TargetType.DOC, [doc.id]).get(doc.id)
    return max(kb_lvl, LEVEL_OF[granted]) if granted else kb_lvl


def doc_levels(conn: Connection, user: User, doc_ids: list[int], kb_lvl: Level) -> dict[int, Level]:
    """批量计算一组文档的有效权限（列表接口用，避免 N+1）。"""
    if kb_lvl >= Level.MANAGE:
        return {doc_id: kb_lvl for doc_id in doc_ids}
    granted = permission_mapper.select_accepted_types(conn, user.id, TargetType.DOC, doc_ids)
    return {doc_id: max(kb_lvl, LEVEL_OF[granted[doc_id]]) if doc_id in granted else kb_lvl for doc_id in doc_ids}


def _not_found(what: str) -> BizError:
    return BizError(f"{what}不存在", ErrorCode.NOT_FOUND, 404)


def _forbidden(message: str = "没有权限执行此操作") -> BizError:
    return BizError(message, ErrorCode.FORBIDDEN, 403)


def require_kb(conn: Connection, user: User, kb_id: int, min_level: Level) -> tuple[KnowledgeBase, Level]:
    """要求对知识库本身至少有 min_level 权限。min_level = NONE 表示只需知识库存在（所有人可见）。"""
    kb = kb_mapper.select_by_id(conn, kb_id)
    if not kb:
        raise _not_found("知识库")
    lvl = kb_level(conn, user, kb)
    if lvl < min_level:
        raise _forbidden()
    return kb, lvl


_DOC_DENIED = {Level.READ: "没有该文档的读权限", Level.WRITE: "没有该文档的写权限", Level.MANAGE: "没有该文档的管理权限"}


def require_doc(conn: Connection, user: User, kb_id: int, doc_id: int, min_level: Level) -> tuple[Document, Level]:
    """要求对文档至少有 min_level 权限，并校验文档确实属于路径中的知识库。"""
    kb = kb_mapper.select_by_id(conn, kb_id)
    doc = document_mapper.select_by_id(conn, doc_id) if kb else None
    if not doc or doc.kb_id != kb_id:
        raise _not_found("文档")
    lvl = doc_level(conn, user, doc, kb_level(conn, user, kb))
    if lvl < min_level:
        raise _forbidden(_DOC_DENIED.get(min_level, "没有权限执行此操作"))
    return doc, lvl


def can_write_in_kb(conn: Connection, user: User, kb_id: int) -> bool:
    """能否在知识库中写入：对知识库有写权限，或对其中某篇文档有写 / 管理权限（上传正文图片用）。"""
    kb, lvl = require_kb(conn, user, kb_id, Level.NONE)
    return lvl >= Level.WRITE or permission_mapper.exists_accepted_doc_in_kb(
        conn, user.id, kb.id, [PermissionType.WRITE, PermissionType.MANAGE]
    )


def readable_scope(conn: Connection, user: User, kb_ids: list[int]) -> tuple[list[int], list[int]]:
    """用户在这些知识库中能读到的范围（RAG 检索用）：
    返回 (整库可读的知识库 id, 额外可读的单篇文档 id)。知识库不存在时 404。"""
    full_kb_ids, doc_ids = [], []
    for kb_id in dict.fromkeys(kb_ids):  # 去重并保持顺序
        kb, lvl = require_kb(conn, user, kb_id, Level.NONE)
        if lvl >= Level.READ:
            full_kb_ids.append(kb.id)
        else:
            doc_ids.extend(permission_mapper.select_accepted_doc_ids_in_kb(conn, user.id, kb.id))
    return full_kb_ids, doc_ids
