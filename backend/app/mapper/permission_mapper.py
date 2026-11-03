"""
PermissionMapper —— permissions 表的全部 SQL，写法与其他 mapper 一致。

每个用户对同一个对象、每种状态最多一条记录（uk_user_target_status）：
- 已生效（accept）的权限和追加申请的更高权限（pending）可以同时存在，审批期间旧权限照常生效；
- 被拒绝后再次邀请 / 申请时复用 reject 记录，重置为 pending。
"""
from sqlalchemy import Connection, RowMapping, text

from app.models.permission import Permission, PermissionStatus, PermissionType, TargetType

BASE_COLUMN_LIST = "id, permission_type, user_id, target_type, target_id, status, initiator_id, created_at, granted_at"

_ACCEPT = PermissionStatus.ACCEPT.value


def _row_to_permission(row: RowMapping | None) -> Permission | None:
    return Permission(**row) if row else None


def _in_clause(prefix: str, values: list) -> tuple[str, dict]:
    """<foreach collection="values" open="(" separator="," close=")">"""
    params = {f"{prefix}{i}": v for i, v in enumerate(values)}
    return "(" + ", ".join(f":{k}" for k in params) + ")", params


# ==================== 查询 select ====================

def select_by_id(conn: Connection, id: int) -> Permission | None:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM permissions WHERE id = :id"
    return _row_to_permission(conn.execute(text(sql), {"id": id}).mappings().first())


def select_by_user_and_target(
    conn: Connection, user_id: int, target_type: TargetType, target_id: int
) -> dict[PermissionStatus, Permission]:
    """用户对某个对象的全部记录，按状态索引（每种状态最多一条）。"""
    sql = f"""
        SELECT {BASE_COLUMN_LIST} FROM permissions
        WHERE user_id = :user_id AND target_type = :target_type AND target_id = :target_id
    """
    params = {"user_id": user_id, "target_type": target_type.value, "target_id": target_id}
    return {p.status: p for p in (Permission(**row) for row in conn.execute(text(sql), params).mappings().all())}


def select_accepted_types(
    conn: Connection, user_id: int, target_type: TargetType, target_ids: list[int]
) -> dict[int, PermissionType]:
    """批量查询用户对一组对象已生效的权限类型：{target_id: permission_type}。"""
    if not target_ids:
        return {}
    in_sql, params = _in_clause("t", target_ids)
    sql = f"""
        SELECT target_id, permission_type FROM permissions
        WHERE user_id = :user_id AND target_type = :target_type AND status = '{_ACCEPT}' AND target_id IN {in_sql}
    """
    rows = conn.execute(text(sql), {**params, "user_id": user_id, "target_type": target_type.value}).all()
    return {r.target_id: PermissionType(r.permission_type) for r in rows}


def select_pending_application_types(
    conn: Connection, user_id: int, target_type: TargetType, target_ids: list[int]
) -> dict[int, PermissionType]:
    """批量查询用户对一组对象待审批的申请：{target_id: 申请的权限类型}，前端显示“审批中”。"""
    if not target_ids:
        return {}
    in_sql, params = _in_clause("t", target_ids)
    sql = f"""
        SELECT target_id, permission_type FROM permissions
        WHERE user_id = :user_id AND initiator_id = :user_id AND target_type = :target_type
          AND status = 'pending' AND target_id IN {in_sql}
    """
    rows = conn.execute(text(sql), {**params, "user_id": user_id, "target_type": target_type.value}).all()
    return {r.target_id: PermissionType(r.permission_type) for r in rows}


def exists_accepted_doc_in_kb(
    conn: Connection, user_id: int, kb_id: int, permission_types: list[PermissionType] | None = None
) -> bool:
    """用户是否对该知识库中的某篇文档有已生效的权限（可限定权限类型）。"""
    params = {"user_id": user_id, "kb_id": kb_id}
    type_sql = ""
    if permission_types:
        in_sql, type_params = _in_clause("pt", [t.value for t in permission_types])
        type_sql = f" AND p.permission_type IN {in_sql}"
        params.update(type_params)
    sql = f"""
        SELECT 1 FROM permissions p JOIN documents d ON d.id = p.target_id
        WHERE p.user_id = :user_id AND p.target_type = 'doc' AND p.status = '{_ACCEPT}' AND d.kb_id = :kb_id{type_sql}
        LIMIT 1
    """
    return conn.execute(text(sql), params).first() is not None


def _build_where(
    user_id: int | None = None,
    target_type: TargetType | None = None,
    target_id: int | None = None,
    status: PermissionStatus | None = None,
) -> tuple[str, dict]:
    conditions, params = [], {}
    for col, value in (("user_id", user_id), ("target_type", target_type), ("target_id", target_id), ("status", status)):
        if value is not None:
            conditions.append(f"{col} = :{col}")
            params[col] = value.value if hasattr(value, "value") else value
    return (" WHERE " + " AND ".join(conditions)) if conditions else "", params


def select_list(conn: Connection, **condition) -> list[Permission]:
    """按条件查询：user_id / target_type / target_id / status，按发起时间倒序。"""
    where, params = _build_where(**condition)
    sql = f"SELECT {BASE_COLUMN_LIST} FROM permissions{where} ORDER BY created_at DESC, id DESC"
    return [Permission(**row) for row in conn.execute(text(sql), params).mappings().all()]


def select_pending_applications_for_manager(conn: Connection, manager_id: int, all_targets: bool = False) -> list[Permission]:
    """待某个管理者审批的申请：申请对象是他管理的知识库（拥有者或 manage 权限）、这些知识库下的文档，
    或他有 manage 权限的单篇文档。all_targets=True（系统管理员）时返回全部待审批申请。"""
    base = f"""
        SELECT {BASE_COLUMN_LIST} FROM permissions p
        WHERE p.status = 'pending' AND p.initiator_id = p.user_id
    """
    if all_targets:
        return [Permission(**row) for row in conn.execute(text(base + " ORDER BY p.created_at DESC")).mappings().all()]
    managed_kbs = f"""
        SELECT id FROM knowledge_bases WHERE owner_id = :uid
        UNION
        SELECT target_id FROM permissions
        WHERE user_id = :uid AND target_type = 'kb' AND status = '{_ACCEPT}' AND permission_type = 'manage'
    """
    sql = f"""{base} AND (
            (p.target_type = 'kb' AND p.target_id IN ({managed_kbs}))
            OR (p.target_type = 'doc' AND (
                p.target_id IN (SELECT id FROM documents WHERE kb_id IN ({managed_kbs}))
                OR p.target_id IN (
                    SELECT target_id FROM permissions
                    WHERE user_id = :uid AND target_type = 'doc' AND status = '{_ACCEPT}' AND permission_type = 'manage'
                )
            ))
        ) ORDER BY p.created_at DESC, p.id DESC
    """
    return [Permission(**row) for row in conn.execute(text(sql), {"uid": manager_id}).mappings().all()]


# ==================== 新增 insert ====================

def insert(
    conn: Connection, permission_type: PermissionType, user_id: int, target_type: TargetType, target_id: int,
    initiator_id: int,
) -> int:
    """新增一条 pending 记录，返回自增主键 id。"""
    sql = """
        INSERT INTO permissions (permission_type, user_id, target_type, target_id, initiator_id)
        VALUES (:permission_type, :user_id, :target_type, :target_id, :initiator_id)
    """
    params = {
        "permission_type": permission_type.value, "user_id": user_id, "target_type": target_type.value,
        "target_id": target_id, "initiator_id": initiator_id,
    }
    return conn.execute(text(sql), params).lastrowid


def insert_accepted(
    conn: Connection, permission_type: PermissionType, user_id: int, target_type: TargetType, target_id: int,
    initiator_id: int,
) -> int:
    """直接写入一条已生效的记录（通过邀请链接加入），返回自增主键 id。"""
    sql = f"""
        INSERT INTO permissions (permission_type, user_id, target_type, target_id, status, initiator_id, granted_at)
        VALUES (:permission_type, :user_id, :target_type, :target_id, '{_ACCEPT}', :initiator_id, CURRENT_TIMESTAMP)
    """
    params = {
        "permission_type": permission_type.value, "user_id": user_id, "target_type": target_type.value,
        "target_id": target_id, "initiator_id": initiator_id,
    }
    return conn.execute(text(sql), params).lastrowid


# ==================== 修改 update ====================

def reset_to_pending(conn: Connection, id: int, permission_type: PermissionType, initiator_id: int) -> int:
    """被拒绝后重新邀请 / 申请：复用原记录，重置为 pending。"""
    sql = """
        UPDATE permissions
        SET permission_type = :permission_type, initiator_id = :initiator_id, status = 'pending',
            created_at = CURRENT_TIMESTAMP, granted_at = NULL
        WHERE id = :id
    """
    return conn.execute(text(sql), {"permission_type": permission_type.value, "initiator_id": initiator_id, "id": id}).rowcount


def update_status(conn: Connection, id: int, status: PermissionStatus) -> int:
    """只能处理 pending 记录（WHERE 中带上 status 条件，防止并发重复处理）；接受时写入授权时间。"""
    granted = "CURRENT_TIMESTAMP" if status == PermissionStatus.ACCEPT else "NULL"
    sql = f"UPDATE permissions SET status = :status, granted_at = {granted} WHERE id = :id AND status = 'pending'"
    return conn.execute(text(sql), {"status": status.value, "id": id}).rowcount


def update_type(conn: Connection, id: int, permission_type: PermissionType) -> int:
    sql = "UPDATE permissions SET permission_type = :permission_type WHERE id = :id"
    return conn.execute(text(sql), {"permission_type": permission_type.value, "id": id}).rowcount


# ==================== 删除 delete ====================

def delete_by_id(conn: Connection, id: int) -> int:
    return conn.execute(text("DELETE FROM permissions WHERE id = :id"), {"id": id}).rowcount


def delete_by_target(conn: Connection, target_type: TargetType, target_id: int) -> int:
    sql = "DELETE FROM permissions WHERE target_type = :target_type AND target_id = :target_id"
    return conn.execute(text(sql), {"target_type": target_type.value, "target_id": target_id}).rowcount


def delete_by_kb(conn: Connection, kb_id: int) -> int:
    """删除知识库时清理：知识库本身的授权 + 其下所有文档的授权（须在删除文档之前执行）。"""
    sql = """
        DELETE FROM permissions
        WHERE (target_type = 'kb' AND target_id = :kb_id)
           OR (target_type = 'doc' AND target_id IN (SELECT id FROM documents WHERE kb_id = :kb_id))
    """
    return conn.execute(text(sql), {"kb_id": kb_id}).rowcount


def delete_by_user_and_target(conn: Connection, user_id: int, target_type: TargetType, target_id: int) -> int:
    sql = "DELETE FROM permissions WHERE user_id = :user_id AND target_type = :target_type AND target_id = :target_id"
    return conn.execute(text(sql), {"user_id": user_id, "target_type": target_type.value, "target_id": target_id}).rowcount
