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
    DOC_IMAGE_MAX_BYTES: int = 10 * 1024 * 1024

    # RAG 文档切分：每块最大字符数、相邻块的重叠字符数
    RAG_CHUNK_SIZE: int = 500
    RAG_CHUNK_OVERLAP: int = 80

    # RAG 向量化：OpenAI 兼容的 /embeddings 接口（示例配置见 app/rag/embedder.py）
    EMBEDDING_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    EMBEDDING_MODEL: str = "text-embedding-v3"
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_DIMENSIONS: int | None = None  # 不填使用模型默认维度
    EMBEDDING_BATCH_SIZE: int = 10
    EMBEDDING_QUERY_INSTRUCTION: str = ""  # 问题的检索指令前缀，bge 系列中文模型可设为 "为这个句子生成表示以用于检索相关文章："
    EMBEDDING_TIMEOUT: float = 30

    @property
    def admin_usernames(self) -> set[str]:
        return {name.strip() for name in self.ADMIN_USERNAMES.split(",") if name.strip()}


settings = Settings()
