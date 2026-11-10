from pydantic import BaseModel, Field

# ---------- 请求体 ----------


class SearchIn(BaseModel):
    """检索内容的空白折叠和长度校验在 retrieval_service 中完成，与配置 RAG_QUESTION_MAX_LENGTH 保持一致"""
    query: str = Field(max_length=10_000, description="检索内容，支持自然语言描述")
    kb_ids: list[int] = Field(min_length=1, max_length=100, description="在哪些知识库中检索")
    top_k: int | None = Field(None, ge=1, description="返回的结果数，不填使用默认值 RAG_TOP_K")


# ---------- 响应体 ----------


class SearchResultOut(BaseModel):
    chunk_id: int
    doc_id: int
    doc_title: str
    kb_id: int
    chunk_index: int
    heading: str  # 章节标题路径，如 "安装 > Linux"
    content: str
    score: float  # 余弦相似度，越大越相关
