from dataclasses import dataclass
from datetime import datetime


@dataclass
class KnowledgeBase:
    """知识库实体，对应 knowledge_bases 表的一行。"""

    id: int
    name: str
    owner_id: int  # 知识库管理员（users.id）
    created_at: datetime
    updated_at: datetime

    def __post_init__(self):
        # SQLite 中时间以字符串返回
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        if isinstance(self.updated_at, str):
            self.updated_at = datetime.fromisoformat(self.updated_at)
