from collections.abc import Generator

from sqlalchemy import Connection, create_engine, text

from app.core.config import BACKEND_DIR, settings

# 这里只把 SQLAlchemy 当作数据库驱动 + 连接池（相当于 Spring 的 DataSource / HikariCP），
# 不使用 ORM，所有 SQL 都在 app/mapper 下手写。
is_sqlite = settings.DATABASE_URL.startswith("sqlite")

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if is_sqlite else {},
)


def get_db() -> Generator[Connection, None, None]:
    """每个请求一个连接（相当于 MyBatis 的 SqlSession）。
    需要显式 conn.commit() 提交；未提交的修改在连接关闭时自动回滚。"""
    with engine.connect() as conn:
        yield conn


def init_db() -> None:
    """启动时检查表结构。SQLite 自动执行 sql/init_sqlite.sql；MySQL 需要先手动执行 sql/init.sql。"""
    with engine.connect() as conn:
        if is_sqlite:
            script = (BACKEND_DIR / "sql" / "init_sqlite.sql").read_text(encoding="utf-8")
            for stmt in filter(str.strip, script.split(";")):
                conn.exec_driver_sql(stmt)
            conn.commit()
        for table in ("users", "knowledge_bases", "documents", "permissions"):
            try:
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"{table} 表不存在，请先执行: mysql -uroot -p < sql/init.sql") from e
        if not is_sqlite:
            _upgrade_permission_unique_key(conn)
            for table in ("notifications", "invite_links", "document_chunks"):
                _create_table_if_missing(conn, table)


def _upgrade_permission_unique_key(conn: Connection) -> None:
    """旧库升级（同 sql/migrate_permission_upgrade.sql）：唯一键放宽为每种状态一条，
    使已生效的权限与追加申请的更高权限可以并存。现有数据满足新约束，无需改动。"""
    old = conn.execute(text("SHOW INDEX FROM permissions WHERE Key_name = 'uk_user_target'")).first()
    if old:
        conn.execute(text(
            "ALTER TABLE permissions DROP INDEX uk_user_target, "
            "ADD UNIQUE KEY uk_user_target_status (user_id, target_type, target_id, status)"
        ))
        conn.commit()


def _create_table_if_missing(conn: Connection, table: str) -> None:
    """旧库升级：执行 sql/init.sql 中该表的建表语句（CREATE TABLE IF NOT EXISTS，已存在时跳过）。"""
    script = (BACKEND_DIR / "sql" / "init.sql").read_text(encoding="utf-8")
    stmt = next(s for s in script.split(";") if f"CREATE TABLE IF NOT EXISTS {table}\n" in s)
    conn.exec_driver_sql(stmt)
    conn.commit()
