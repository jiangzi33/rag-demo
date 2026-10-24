from dataclasses import dataclass
from datetime import datetime


@dataclass
class Document:
    """文档实体，对应 documents 表的一行。"""

    id: int
    kb_id: int  # 所在知识库（knowledge_bases.id）
    title: str
    content: str
    author_id: int  # 文档作者（users.id）
    published_at: datetime
    updated_at: datetime

    def __post_init__(self):
        # SQLite 中时间以字符串返回
        if isinstance(self.published_at, str):
            self.published_at = datetime.fromisoformat(self.published_at)
        if isinstance(self.updated_at, str):
            self.updated_at = datetime.fromisoformat(self.updated_at)
