from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class ChatIn(BaseModel):
    """问题的空白折叠和长度校验在 retrieval_service 中完成，与配置 RAG_QUESTION_MAX_LENGTH 保持一致"""
    question: str = Field(max_length=10_000)
    kb_ids: list[int] = Field(min_length=1, max_length=100, description="在哪些知识库中检索")
    history: list[ChatMessage] = Field(default_factory=list, max_length=50, description="之前的对话，用于理解追问")
    top_k: int | None = Field(None, ge=1, description="检索的片段数，不填使用 QA_TOP_K")
