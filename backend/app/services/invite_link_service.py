"""
邀请链接：管理者生成一个可分享的链接，登录用户打开即直接获得对应权限并跳转到目标对象。

    管理者 ──create──▶ 链接(token) ──分享──▶ 用户打开 /#/invite/{token} ──redeem──▶ 权限生效 ──▶ 跳转到知识库 / 文档

- 生成、查看、撤销链接都需要对象的管理权限；撤销即删除
- 链接可设置有效期和使用次数；生成人失去管理权限（被撤销、知识库转让等）后链接自动失效，避免越权授予
- 打开链接视同接受邀请，不再走 pending 流程：
  已拥有同等或更高的有效权限（含知识库授权覆盖到文档、拥有者）时不做改动，直接跳转，也不占用次数；
  否则写入 / 提升为链接的权限，并清理被它覆盖的待处理请求（更高权限的待处理请求保留）
- 有人通过链接获得权限时通知链接生成人
"""
import secrets
from datetime import datetime, timedelta

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import BizError, ErrorCode
from app.mapper import document_mapper, invite_link_mapper, permission_mapper, user_mapper
from app.models.invite_link import InviteLink
from app.models.knowledge_base import KnowledgeBase
from app.models.notification import NotificationType
from app.models.permission import PermissionStatus, TargetType
from app.models.user import User, UserStatus
from app.schemas.permission import InviteLinkIn, InviteLinkOut, InviteLinkRedeemOut
from app.schemas.user import UserBriefOut
from app.services import access_service as access
from app.services import notification_service
from app.services.access_service import Level
from app.services.permission_service import TYPE_LABEL, _conflict, _level_on, _require_manage


def _invalid(message: str) -> BizError:
    return BizError(message, ErrorCode.INVITE_LINK_INVALID)


# ---------- 管理 ----------

def create(conn: Connection, user: User, data: InviteLinkIn) -> InviteLink:
    _require_manage(conn, user, data.target_type, data.target_id)
    expires_at = datetime.now().replace(microsecond=0) + timedelta(days=data.expires_in_days) if data.expires_in_days else None
    link_id = invite_link_mapper.insert(
        conn, secrets.token_urlsafe(16), data.target_type, data.target_id, data.permission_type, user.id,
        expires_at, data.max_uses,
    )
    conn.commit()
    return invite_link_mapper.select_by_id(conn, link_id)


def list_by_target(conn: Connection, user: User, target_type: TargetType, target_id: int) -> list[InviteLink]:
    _require_manage(conn, user, target_type, target_id)
    return invite_link_mapper.select_by_target(conn, target_type, target_id)


def remove(conn: Connection, user: User, link_id: int) -> None:
    link = invite_link_mapper.select_by_id(conn, link_id)
    if not link:
        raise BizError("邀请链接不存在", ErrorCode.NOT_FOUND, 404)
    _require_manage(conn, user, link.target_type, link.target_id)
    invite_link_mapper.delete_by_id(conn, link.id)
    conn.commit()


def to_out(conn: Connection, items: list[InviteLink]) -> list[InviteLinkOut]:
    creators = {u.id: UserBriefOut.model_validate(u) for u in user_mapper.select_by_ids(conn, list({l.creator_id for l in items}))}
    return [
        InviteLinkOut(
            id=l.id, token=l.token, target_type=l.target_type, target_id=l.target_id, permission_type=l.permission_type,
            creator_id=l.creator_id, creator=creators.get(l.creator_id), expires_at=l.expires_at, max_uses=l.max_uses,
            use_count=l.use_count, valid=not (l.is_expired or l.is_exhausted), created_at=l.created_at,
        )
        for l in items
    ]


# ---------- 使用 ----------

def _target_info(conn: Connection, target_type: TargetType, target_id: int, kb: KnowledgeBase) -> tuple[str, int]:
    """(对象名称, 所在知识库 id)"""
    if target_type == TargetType.KB:
        return kb.name, kb.id
    doc = document_mapper.select_by_id(conn, target_id)
    return doc.title, doc.kb_id


def redeem(conn: Connection, user: User, token: str) -> InviteLinkRedeemOut:
    link = invite_link_mapper.select_by_token(conn, token)
    if not link:
        raise BizError("邀请链接不存在或已被撤销", ErrorCode.NOT_FOUND, 404)
    if link.is_expired:
        raise _invalid("邀请链接已过期")
    kb, lvl = _level_on(conn, user, link.target_type, link.target_id)  # 对象已删除时 404
    name, kb_id = _target_info(conn, link.target_type, link.target_id, kb)
    need = access.LEVEL_OF[link.permission_type]

    def result(granted: bool, level: Level) -> InviteLinkRedeemOut:
        return InviteLinkRedeemOut(
            target_type=link.target_type, target_id=link.target_id, target_name=name, kb_id=kb_id,
            permission_type=link.permission_type, granted=granted, my_permission=level.label,
        )

    if lvl >= need:  # 已拥有同等或更高权限：直接跳转，不占用次数
        return result(False, lvl)
    if link.is_exhausted:
        raise _invalid("邀请链接的使用次数已用完")
    creator = user_mapper.select_by_id(conn, link.creator_id)
    if not creator or creator.status != UserStatus.NORMAL or _level_on(conn, creator, link.target_type, link.target_id)[1] < Level.MANAGE:
        raise _invalid("邀请人已不再管理该对象，链接已失效")

    records = permission_mapper.select_by_user_and_target(conn, user.id, link.target_type, link.target_id)
    pending = records.get(PermissionStatus.PENDING)
    if pending and access.LEVEL_OF[pending.permission_type] <= need:  # 被链接权限覆盖的待处理邀请 / 申请
        permission_mapper.delete_by_id(conn, pending.id)
    if granted := records.get(PermissionStatus.ACCEPT):  # 原有较低的直接授权，替换为链接的权限
        permission_mapper.delete_by_id(conn, granted.id)
    try:
        permission_mapper.insert_accepted(conn, link.permission_type, user.id, link.target_type, link.target_id, creator.id)
    except IntegrityError as e:  # 并发重复打开
        conn.rollback()
        raise _conflict("正在处理，请刷新后重试") from e
    if not invite_link_mapper.increment_use(conn, link.id):  # 并发下次数已被用完
        conn.rollback()
        raise _invalid("邀请链接的使用次数已用完")

    target = f"知识库「{name}」" if link.target_type == TargetType.KB else f"文档「{name}」"
    notification_service.notify(
        conn, creator.id, user, NotificationType.LINK_JOINED,
        f"通过邀请链接获得了{target}的{TYPE_LABEL[link.permission_type]}权限", link.target_type.value, link.target_id, kb_id,
    )
    conn.commit()
    return result(True, need)
