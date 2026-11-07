import json
from dataclasses import dataclass
from datetime import datetime


@dataclass
class DocumentChunk:
    """文档切分块实体，对应 document_chunks 表的一行。"""

    id: int
    doc_id: int  # 所属文档（documents.id）
    kb_id: int  # 所属知识库（knowledge_bases.id），检索时按知识库过滤
    chunk_index: int  # 在文档内的序号，从 0 开始
    heading_path: list[str]  # 所在章节的标题路径，库中存 JSON 数组
    content: str
    created_at: datetime

    def __post_init__(self):
        if isinstance(self.heading_path, str):
            self.heading_path = json.loads(self.heading_path)
        # SQLite 中时间以字符串返回
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
