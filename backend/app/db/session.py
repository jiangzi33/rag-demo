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
        for table in ("users", "knowledge_bases", "documents"):
            try:
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"{table} 表不存在，请先执行: mysql -uroot -p < sql/init.sql") from e
