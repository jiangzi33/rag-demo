from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "知识库管理系统"
    DATABASE_URL: str = "mysql+pymysql://root:password@127.0.0.1:3306/knowledge_base?charset=utf8mb4"

    JWT_SECRET: str = "dev-only-secret-please-change-me-0123456789abcdef"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24

    # 逗号分隔的管理员用户名
    ADMIN_USERNAMES: str = "admin"

    UPLOAD_DIR: Path = BACKEND_DIR / "uploads"
    FRONTEND_DIR: Path = PROJECT_DIR / "frontend"
    AVATAR_MAX_BYTES: int = 2 * 1024 * 1024

    @property
    def admin_usernames(self) -> set[str]:
        return {name.strip() for name in self.ADMIN_USERNAMES.split(",") if name.strip()}


settings = Settings()
