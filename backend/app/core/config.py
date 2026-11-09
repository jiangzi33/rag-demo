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
    RAG_QUESTION_MAX_LENGTH: int = 1000  # 用户问题的最大字符数

    # RAG 向量化：本地 Ollama（见 app/rag/embedder.py）
    EMBEDDING_ENABLED: bool = True  # 关闭后新建文档不再做向量化（测试 / Ollama 未启动时）
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_EMBEDDING_MODEL: str = "qwen3-embedding:0.6b"
    OLLAMA_KEEP_ALIVE: str | None = None  # 模型在内存中保留多久，如 "30m"；不填用 Ollama 默认（5 分钟）
    EMBEDDING_DIMENSIONS: int | None = None  # 不填使用模型默认维度（qwen3-embedding:0.6b 为 1024）
    EMBEDDING_BATCH_SIZE: int = 16
    # 问题的检索指令前缀（文档不加），qwen3-embedding 官方推荐格式；换成 bge 系列可改为 "为这个句子生成表示以用于检索相关文章："
    EMBEDDING_QUERY_INSTRUCTION: str = (
        "Instruct: Given a question, retrieve relevant passages from the knowledge base that answer the question\nQuery: "
    )
    EMBEDDING_TIMEOUT: float = 60

    # RAG 向量库：Milvus（EMBEDDING_ENABLED 关闭时也不读写 Milvus）
    MILVUS_URI: str = "http://localhost:19530"
    MILVUS_TOKEN: str = ""  # 开启认证时填 "用户名:密码"
    MILVUS_COLLECTION: str = "kb_document_chunks"

    @property
    def admin_usernames(self) -> set[str]:
        return {name.strip() for name in self.ADMIN_USERNAMES.split(",") if name.strip()}


settings = Settings()
