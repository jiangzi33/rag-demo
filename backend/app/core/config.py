from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "知识库管理系统"
    LOG_LEVEL: str = "INFO"  # 应用日志（app.*）的级别：DEBUG / INFO / WARNING / ERROR
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
    RAG_TOP_K: int = 5  # 默认返回最相似的片段数
    RAG_TOP_K_MAX: int = 20
    RAG_MIN_SCORE: float | None = None  # 余弦相似度下限，低于它且没有命中关键词的片段丢弃；不填不过滤
    # 混合检索：语义检索 + 关键词检索（BM25）各取一批候选，按 RRF 融合排序。vector 为只用语义检索
    RAG_SEARCH_MODE: Literal["hybrid", "vector"] = "hybrid"
    RAG_HYBRID_CANDIDATES: int = 20  # 每一路的候选数（至少为 top_k）
    RAG_RRF_K: int = 60  # RRF 常数：越大，排名靠后的候选与靠前的差距越小
    RAG_KEYWORD_WEIGHT: float = 1.0  # 关键词检索在融合中的权重（语义检索为 1）
    RAG_LOG_RESULTS: bool = True  # 每次检索打印日志：问题、两路候选数、耗时和每条结果的得分
    RAG_LOG_CONTENT_CHARS: int = 60  # 日志中每条结果的正文预览字数，0 为不打印正文

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

    # 智能问答：检索片段后调用 OpenAI（或兼容 OpenAI 的服务）生成回答，见 app/rag/llm.py
    OPENAI_API_KEY: str = " "
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4.1-mini"
    OPENAI_TEMPERATURE: float | None = None  # 不填用模型默认值（部分推理模型不支持自定义 temperature）
    OPENAI_TIMEOUT: float = 120
    QA_TOP_K: int = 5  # 每个问题检索的片段数（交给大模型的参考资料条数）
    QA_HISTORY_MESSAGES: int = 6  # 携带的最近对话条数（用户 + 助手），用于理解追问
    # Query Rewrite：检索前用大模型把模糊提问、追问改写成独立清晰的检索问题，见 app/services/query_rewrite_service.py
    QA_REWRITE_ENABLED: bool = True
    QA_REWRITE_MODEL: str | None = None  # 改写用的模型，不填与 OPENAI_MODEL 相同；可换成更快更便宜的小模型
    QA_REWRITE_TIMEOUT: float = 15  # 改写超时秒数，超时回退为不改写，不影响问答
    QA_REWRITE_HISTORY_CHARS: int = 300  # 改写时每条历史回答最多携带的字数（只用于理解上下文，不需要全文）
    # 文档摘要：基于文档的切分块生成摘要（标注片段出处），见 app/services/summary_service.py
    SUMMARY_DIRECT_CHARS: int = 12000  # 切分块总字数不超过它时一次交给大模型；超过时先分批提炼要点再合并（map-reduce）
    SUMMARY_BATCH_CHARS: int = 6000  # 分批提炼时每批的字数
    SUMMARY_FOCUS_TOP_K: int = 8  # 指定关注点时，在文档内检索的片段数
    SUMMARY_MAX_CHUNKS: int = 400  # 最多使用文档前多少个切分块，避免超长文档调用过多次大模型
    # 生成评估：给回答打分（忠实度 / 相关性 / 完整性 / 正确性）的裁判模型，不填与 OPENAI_MODEL 相同；
    # 建议用比生成模型更强的模型，避免“自己给自己打分”偏高，见 app/services/generation_eval_service.py
    EVAL_JUDGE_MODEL: str | None = None

    @property
    def admin_usernames(self) -> set[str]:
        return {name.strip() for name in self.ADMIN_USERNAMES.split(",") if name.strip()}


settings = Settings()
