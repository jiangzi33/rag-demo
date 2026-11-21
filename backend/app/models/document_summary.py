import json
from dataclasses import dataclass
from datetime import datetime


@dataclass
class DocumentSummary:
    """文档摘要实体，对应 document_summaries 表的一行（大模型生成结果的缓存）。"""

    id: int
    doc_id: int
    kb_id: int
    focus: str  # 关注点，空字符串为全文摘要
    fingerprint: str  # 生成时文档标题、正文和切分块 id 的 SHA-256，与当前不一致时摘要失效
    mode: str  # full / map_reduce / focus
    stale: bool  # 是否基于临时切分
    truncated: bool  # 是否只总结了前面的部分
    sources: list[dict]  # 出处片段，库中存 JSON 数组
    summary: str
    model: str
    created_at: datetime

    def __post_init__(self):
        if isinstance(self.sources, str):
            self.sources = json.loads(self.sources)
        self.stale, self.truncated = bool(self.stale), bool(self.truncated)
        # SQLite 中时间以字符串返回
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
