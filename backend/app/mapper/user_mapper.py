"""
UserMapper —— users 表的全部 SQL，相当于 MyBatis 的 UserMapper 接口 + UserMapper.xml。

与 MyBatis 的对应关系：
    :name 占位符            <->  #{name}（预编译参数，防 SQL 注入；SQL 中绝不拼接用户输入）
    BASE_COLUMN_LIST        <->  <sql id="Base_Column_List">
    _row_to_user()          <->  <resultMap type="User">
    _build_where()          <->  <where><if test="..."> 动态 SQL
    _build_set()            <->  <set><if test="..."> 动态 SQL
    insert() 返回 lastrowid <->  useGeneratedKeys="true" keyProperty="id"
    update_*() 返回 rowcount <->  int updateXxx(...) 返回受影响行数

所有方法的第一个参数 conn 相当于 SqlSession，事务由 service 层负责提交。
"""
from sqlalchemy import Connection, RowMapping, text

from app.models.user import User

BASE_COLUMN_LIST = "id, username, password, email, nickname, avatar, created_at, is_vip, status"

# 允许动态更新的列（白名单），防止把任意键名拼进 SQL
_UPDATABLE_COLUMNS = {"nickname", "email", "avatar", "password", "status", "is_vip"}


def _row_to_user(row: RowMapping | None) -> User | None:
    return User(**row) if row else None


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，配合 ESCAPE '!' 使用，让用户输入的 % _ 按普通字符匹配。"""
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


# ==================== 查询 select ====================

def select_by_id(conn: Connection, id: int) -> User | None:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM users WHERE id = :id"
    return _row_to_user(conn.execute(text(sql), {"id": id}).mappings().first())


def select_by_username(conn: Connection, username: str) -> User | None:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM users WHERE username = :username"
    return _row_to_user(conn.execute(text(sql), {"username": username}).mappings().first())


def select_by_email(conn: Connection, email: str) -> User | None:
    sql = f"SELECT {BASE_COLUMN_LIST} FROM users WHERE email = :email"
    return _row_to_user(conn.execute(text(sql), {"email": email}).mappings().first())


def select_by_ids(conn: Connection, ids: list[int]) -> list[User]:
    """相当于 <foreach collection="ids" open="(" separator="," close=")">；每个 id 都是独立的绑定参数。"""
    if not ids:
        return []
    params = {f"id{i}": v for i, v in enumerate(ids)}
    placeholders = ", ".join(f":{k}" for k in params)
    sql = f"SELECT {BASE_COLUMN_LIST} FROM users WHERE id IN ({placeholders})"
    return [User(**row) for row in conn.execute(text(sql), params).mappings().all()]


def _build_where(keyword: str | None, status: int | None, is_vip: bool | None) -> tuple[str, dict]:
    """
    等价于：
    <where>
      <if test="keyword != null and keyword != ''">
        AND (username LIKE #{kw} OR nickname LIKE #{kw} OR email LIKE #{kw})
      </if>
      <if test="status != null"> AND status = #{status} </if>
      <if test="isVip != null"> AND is_vip = #{isVip} </if>
    </where>
    """
    conditions, params = [], {}
    if keyword and keyword.strip():
        conditions.append(
            "(username LIKE :kw ESCAPE '!' OR nickname LIKE :kw ESCAPE '!' OR email LIKE :kw ESCAPE '!')"
        )
        params["kw"] = f"%{_escape_like(keyword.strip())}%"
    if status is not None:
        conditions.append("status = :status")
        params["status"] = int(status)
    if is_vip is not None:
        conditions.append("is_vip = :is_vip")
        params["is_vip"] = int(is_vip)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


def count_by_condition(conn: Connection, keyword: str | None, status: int | None, is_vip: bool | None) -> int:
    where, params = _build_where(keyword, status, is_vip)
    sql = f"SELECT COUNT(*) FROM users{where}"
    return conn.execute(text(sql), params).scalar_one()


def select_page(
    conn: Connection, keyword: str | None, status: int | None, is_vip: bool | None, offset: int, limit: int
) -> list[User]:
    where, params = _build_where(keyword, status, is_vip)
    sql = f"SELECT {BASE_COLUMN_LIST} FROM users{where} ORDER BY id DESC LIMIT :limit OFFSET :offset"
    rows = conn.execute(text(sql), {**params, "limit": limit, "offset": offset}).mappings().all()
    return [User(**row) for row in rows]


# ==================== 新增 insert ====================

def insert(conn: Connection, username: str, password: str, email: str, nickname: str) -> int:
    """插入用户，返回自增主键 id。created_at / is_vip / status 使用数据库默认值。"""
    sql = """
        INSERT INTO users (username, password, email, nickname)
        VALUES (:username, :password, :email, :nickname)
    """
    result = conn.execute(
        text(sql), {"username": username, "password": password, "email": email, "nickname": nickname}
    )
    return result.lastrowid


# ==================== 修改 update ====================

def _build_set(fields: dict) -> tuple[str, dict]:
    """
    等价于：
    <set>
      <if test="nickname != null"> nickname = #{nickname}, </if>
      <if test="email != null"> email = #{email}, </if>
      ...
    </set>
    """
    fields = {k: v for k, v in fields.items() if v is not None}
    illegal = fields.keys() - _UPDATABLE_COLUMNS
    if illegal:
        raise ValueError(f"不允许更新的列: {illegal}")
    return ", ".join(f"{col} = :{col}" for col in fields), fields


def update_selective(conn: Connection, id: int, **fields) -> int:
    """按需更新非空字段（相当于 MyBatis-Generator 的 updateByPrimaryKeySelective），返回受影响行数。"""
    set_clause, params = _build_set(fields)
    if not set_clause:
        return 0
    sql = f"UPDATE users SET {set_clause} WHERE id = :id"
    return conn.execute(text(sql), {**params, "id": id}).rowcount


def update_password(conn: Connection, id: int, password: str) -> int:
    sql = "UPDATE users SET password = :password WHERE id = :id"
    return conn.execute(text(sql), {"password": password, "id": id}).rowcount


def update_avatar(conn: Connection, id: int, avatar: str) -> int:
    sql = "UPDATE users SET avatar = :avatar WHERE id = :id"
    return conn.execute(text(sql), {"avatar": avatar, "id": id}).rowcount


def update_status(conn: Connection, id: int, status: int) -> int:
    """修改状态；“注销”是逻辑删除，即 status = 2。"""
    sql = "UPDATE users SET status = :status WHERE id = :id"
    return conn.execute(text(sql), {"status": int(status), "id": id}).rowcount


def update_vip(conn: Connection, id: int, is_vip: bool) -> int:
    sql = "UPDATE users SET is_vip = :is_vip WHERE id = :id"
    return conn.execute(text(sql), {"is_vip": int(is_vip), "id": id}).rowcount


# ==================== 删除 delete ====================

def delete_by_id(conn: Connection, id: int) -> int:
    """物理删除。业务上用户注销走逻辑删除（update_status），此方法仅供后台数据清理使用。"""
    sql = "DELETE FROM users WHERE id = :id"
    return conn.execute(text(sql), {"id": id}).rowcount
