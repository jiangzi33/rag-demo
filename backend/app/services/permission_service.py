"""
权限授予流程（邀请 / 申请）：

    邀请：管理者 ──invite──▶ pending ──被邀请人 accept / reject──▶ 生效 / 拒绝
    申请：用户   ──apply───▶ pending ──管理者 accept / reject────▶ 生效 / 拒绝

- 对某个对象有“管理”权限（或是知识库拥有者 / 系统管理员）才能邀请、审批、修改、撤销该对象的授权；
  知识库的管理者同时管理其下所有文档的授权
- 同一用户对同一对象同时只能有一个待处理请求；被拒绝后可以重新发起
- 已有权限时可以追加申请 / 邀请更高的权限：审批期间原权限照常生效，通过后替换为新权限，被拒绝则保持不变
- 删除授权记录即撤销权限；被授权人可以放弃自己的权限，发起人可以撤回自己发起的待处理请求
- 别人变更了我的权限（邀请、审批结果、调整、撤销 / 撤回）时，在同一事务中给我发站内通知
"""
from dataclasses import asdict

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import BizError, ErrorCode
from app.mapper import document_mapper
from app.mapper import knowledge_base_mapper as kb_mapper
from app.mapper import permission_mapper, user_mapper
from app.models.knowledge_base import KnowledgeBase
from app.models.notification import NotificationType
from app.models.permission import Permission, PermissionStatus, PermissionType, TargetType
from app.models.user import User, UserStatus
from app.schemas.permission import ApplicationIn, InvitationIn, PermissionOut
from app.schemas.user import UserBriefOut
from app.services import access_service as access
from app.services import notification_service
from app.services.access_service import Level

TYPE_LABEL = {PermissionType.READ: "读", PermissionType.WRITE: "写", PermissionType.MANAGE: "管理"}


def _conflict(message: str) -> BizError:
    return BizError(message, ErrorCode.PERMISSION_CONFLICT)


def _load_target(conn: Connection, target_type: TargetType, target_id: int) -> tuple[KnowledgeBase, int | None]:
    """返回 (对象所在知识库, 文档 id 或 None)；对象不存在时 404。"""
    if target_type == TargetType.KB:
        kb = kb_mapper.select_by_id(conn, target_id)
        if not kb:
            raise BizError("知识库不存在", ErrorCode.NOT_FOUND, 404)
        return kb, None
    doc = document_mapper.select_by_id(conn, target_id)
    if not doc:
        raise BizError("文档不存在", ErrorCode.NOT_FOUND, 404)
    return kb_mapper.select_by_id(conn, doc.kb_id), doc.id


def _level_on(conn: Connection, user: User, target_type: TargetType, target_id: int) -> tuple[KnowledgeBase, Level]:
    kb, doc_id = _load_target(conn, target_type, target_id)
    kb_lvl = access.kb_level(conn, user, kb)
    if doc_id is None:
        return kb, kb_lvl
    return kb, access.doc_levels(conn, user, [doc_id], kb_lvl)[doc_id]


def _require_manage(conn: Connection, user: User, target_type: TargetType, target_id: int) -> KnowledgeBase:
    kb, lvl = _level_on(conn, user, target_type, target_id)
    if lvl < Level.MANAGE:
        raise BizError("需要管理权限才能管理授权", ErrorCode.FORBIDDEN, 403)
    return kb


def _notify(conn: Connection, p: Permission, actor: User, type: NotificationType, template: str) -> None:
    """通知被授权人；template 中的 {target} 替换为对象名称，如“文档「安装指南」”。"""
    if p.target_type == TargetType.KB:
        kb = kb_mapper.select_by_id(conn, p.target_id)
        target, kb_id = (f"知识库「{kb.name}」", kb.id) if kb else ("已删除的知识库", None)
    else:
        doc = document_mapper.select_by_id(conn, p.target_id)
        target, kb_id = (f"文档「{doc.title}」", doc.kb_id) if doc else ("已删除的文档", None)
    notification_service.notify(
        conn, p.user_id, actor, type, template.format(target=target), p.target_type.value, p.target_id, kb_id
    )


def _get_or_404(conn: Connection, permission_id: int) -> Permission:
    p = permission_mapper.select_by_id(conn, permission_id)
    if not p:
        raise BizError("授权记录不存在", ErrorCode.NOT_FOUND, 404)
    return p


def _create_or_reset(
    conn: Connection, user_id: int, target_type: TargetType, target_id: int, ptype: PermissionType, initiator_id: int
) -> int:
    """写入待处理记录（不提交，由调用方提交）。"""
    records = permission_mapper.select_by_user_and_target(conn, user_id, target_type, target_id)
    if PermissionStatus.PENDING in records:
        raise _conflict("已有待处理的邀请或申请")
    granted = records.get(PermissionStatus.ACCEPT)
    if granted and access.LEVEL_OF[granted.permission_type] >= access.LEVEL_OF[ptype]:
        raise _conflict(f"已拥有{TYPE_LABEL[granted.permission_type]}权限，只能追加更高的权限")
    rejected = records.get(PermissionStatus.REJECT)
    try:
        if rejected:  # 之前被拒绝：复用记录重新发起
            permission_mapper.reset_to_pending(conn, rejected.id, ptype, initiator_id)
            pid = rejected.id
        else:  # 首次发起，或在已有权限的基础上追加更高权限（旧的 accept 记录保留，审批期间照常生效）
            pid = permission_mapper.insert(conn, ptype, user_id, target_type, target_id, initiator_id)
    except IntegrityError as e:  # 并发重复发起，uk_user_target_status 冲突
        conn.rollback()
        raise _conflict("已有待处理的邀请或申请") from e
    return pid


# ---------- 发起 ----------

def invite(conn: Connection, inviter: User, data: InvitationIn) -> Permission:
    kb = _require_manage(conn, inviter, data.target_type, data.target_id)
    grantee = user_mapper.select_by_username(conn, data.username.strip())
    if not grantee or grantee.status != UserStatus.NORMAL:
        raise BizError("用户不存在或不可用", ErrorCode.NOT_FOUND, 404)
    if grantee.id == inviter.id:
        raise _conflict("不能邀请自己")
    if grantee.id == kb.owner_id:
        raise _conflict("该用户是知识库拥有者，已拥有全部权限")
    pid = _create_or_reset(conn, grantee.id, data.target_type, data.target_id, data.permission_type, inviter.id)
    p = permission_mapper.select_by_id(conn, pid)
    _notify(conn, p, inviter, NotificationType.PERM_INVITED, f"邀请你获得{{target}}的{TYPE_LABEL[p.permission_type]}权限")
    conn.commit()
    return p


def apply(conn: Connection, user: User, data: ApplicationIn) -> Permission:
    _, lvl = _level_on(conn, user, data.target_type, data.target_id)
    if lvl >= access.LEVEL_OF[data.permission_type]:
        raise _conflict("你已拥有该权限")
    pid = _create_or_reset(conn, user.id, data.target_type, data.target_id, data.permission_type, user.id)
    conn.commit()
    return permission_mapper.select_by_id(conn, pid)


# ---------- 处理 ----------

def _require_handler(conn: Connection, user: User, p: Permission) -> None:
    """邀请由被邀请人处理；申请由对象的管理者处理（申请人自己不能审批）。"""
    if p.status != PermissionStatus.PENDING:
        raise _conflict("该请求已处理")
    if p.is_application:
        if p.user_id == user.id:
            raise BizError("不能审批自己的申请", ErrorCode.FORBIDDEN, 403)
        _require_manage(conn, user, p.target_type, p.target_id)
    elif p.user_id != user.id:
        raise BizError("只有被邀请人可以处理邀请", ErrorCode.FORBIDDEN, 403)


def _decide(conn: Connection, user: User, permission_id: int, status: PermissionStatus) -> Permission:
    p = _get_or_404(conn, permission_id)
    _require_handler(conn, user, p)
    # 每种状态只保留一条：通过时新权限替换旧权限，拒绝时替换旧的拒绝记录（同一事务，失败一起回滚）
    old = permission_mapper.select_by_user_and_target(conn, p.user_id, p.target_type, p.target_id).get(status)
    if old:
        if status == PermissionStatus.ACCEPT and access.LEVEL_OF[old.permission_type] > access.LEVEL_OF[p.permission_type]:
            permission_mapper.update_type(conn, p.id, old.permission_type)  # 审批期间权限已被调高，不降级
        permission_mapper.delete_by_id(conn, old.id)
    if not permission_mapper.update_status(conn, p.id, status):  # 已被并发处理
        conn.rollback()
        raise _conflict("该请求已处理")
    p = permission_mapper.select_by_id(conn, p.id)
    if p.is_application:  # 管理者审批了申请，通知申请人（邀请由本人处理，无需通知）
        label = TYPE_LABEL[p.permission_type]
        if status == PermissionStatus.ACCEPT:
            _notify(conn, p, user, NotificationType.PERM_APPROVED, f"同意了你对{{target}}的{label}权限申请")
        else:
            _notify(conn, p, user, NotificationType.PERM_REJECTED, f"驳回了你对{{target}}的{label}权限申请")
    conn.commit()
    return p


def accept(conn: Connection, user: User, permission_id: int) -> Permission:
    return _decide(conn, user, permission_id, PermissionStatus.ACCEPT)


def reject(conn: Connection, user: User, permission_id: int) -> Permission:
    return _decide(conn, user, permission_id, PermissionStatus.REJECT)


def change_type(conn: Connection, user: User, permission_id: int, ptype: PermissionType) -> Permission:
    """管理者调整权限类型（待处理或已生效的记录都可以）。"""
    p = _get_or_404(conn, permission_id)
    _require_manage(conn, user, p.target_type, p.target_id)
    if p.user_id == user.id:
        raise BizError("不能修改自己的权限", ErrorCode.FORBIDDEN, 403)
    if p.status == PermissionStatus.REJECT:
        raise _conflict("已拒绝的记录不能修改，请重新邀请")
    if ptype == p.permission_type:
        return p
    permission_mapper.update_type(conn, p.id, ptype)
    old, new = TYPE_LABEL[p.permission_type], TYPE_LABEL[ptype]
    if p.status == PermissionStatus.ACCEPT:
        template = f"将你对{{target}}的权限由{old}调整为{new}"
    elif p.is_application:
        template = f"将你对{{target}}的权限申请由{old}调整为{new}"
    else:
        template = f"将邀请你获得的{{target}}权限由{old}调整为{new}"
    _notify(conn, p, user, NotificationType.PERM_CHANGED, template)
    conn.commit()
    return permission_mapper.select_by_id(conn, p.id)


def remove(conn: Connection, user: User, permission_id: int) -> None:
    """撤销 / 放弃 / 撤回：被授权人本人、待处理请求的发起人，或对象的管理者。"""
    p = _get_or_404(conn, permission_id)
    is_self = p.user_id == user.id
    is_pending_initiator = p.status == PermissionStatus.PENDING and p.initiator_id == user.id
    if not (is_self or is_pending_initiator):
        _require_manage(conn, user, p.target_type, p.target_id)
    permission_mapper.delete_by_id(conn, p.id)
    label = TYPE_LABEL[p.permission_type]
    if p.status == PermissionStatus.ACCEPT:
        _notify(conn, p, user, NotificationType.PERM_REVOKED, f"撤销了你对{{target}}的{label}权限")
    elif p.status == PermissionStatus.PENDING:
        if p.is_application:
            template = f"删除了你对{{target}}的{label}权限申请"
        else:
            template = f"撤回了邀请你获得{{target}}{label}权限的邀请"
        _notify(conn, p, user, NotificationType.PERM_WITHDRAWN, template)
    conn.commit()


# ---------- 查询 ----------

def list_mine(conn: Connection, user: User, status: PermissionStatus | None) -> list[Permission]:
    """与我有关的授权：别人对我的邀请、我的申请、我已获得的权限。"""
    return permission_mapper.select_list(conn, user_id=user.id, status=status)


def list_by_target(conn: Connection, user: User, target_type: TargetType, target_id: int) -> list[Permission]:
    """某个知识库 / 文档的全部授权记录（成员管理），需要管理权限。"""
    _require_manage(conn, user, target_type, target_id)
    return permission_mapper.select_list(conn, target_type=target_type, target_id=target_id)


def list_approvals(conn: Connection, user: User) -> list[Permission]:
    """待我审批的申请。"""
    return permission_mapper.select_pending_applications_for_manager(conn, user.id, access.is_system_admin(user))


def to_out(conn: Connection, items: list[Permission]) -> list[PermissionOut]:
    """批量补齐用户信息和对象名称，避免 N+1。"""
    users = {
        u.id: UserBriefOut.model_validate(u)
        for u in user_mapper.select_by_ids(conn, list({p.user_id for p in items} | {p.initiator_id for p in items}))
    }
    kbs = {
        kb.id: kb
        for kb in kb_mapper.select_by_ids(conn, list({p.target_id for p in items if p.target_type == TargetType.KB}))
    }
    docs = {
        d["id"]: d
        for d in document_mapper.select_brief_by_ids(conn, list({p.target_id for p in items if p.target_type == TargetType.DOC}))
    }
    result = []
    for p in items:
        if p.target_type == TargetType.KB:
            kb = kbs.get(p.target_id)
            name, kb_id = (kb.name, kb.id) if kb else (None, None)
        else:
            doc = docs.get(p.target_id)
            name, kb_id = (doc["title"], doc["kb_id"]) if doc else (None, None)
        result.append(PermissionOut(
            **asdict(p), user=users.get(p.user_id), initiator=users.get(p.initiator_id),
            target_name=name, kb_id=kb_id, direction="apply" if p.is_application else "invite",
        ))
    return result
