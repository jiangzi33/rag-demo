"""
RAG 第一步：把文档切分成适合做向量检索的文本块（chunk）。

流程：
1. HTML → 结构化块（见 html_parser）
2. 按标题把文档分成若干章节，每个章节记录自己的标题路径（如 ["安装", "Linux"]）
3. 章节内的块依次装入 chunk，不超过 chunk_size 个字符；相邻 chunk 之间保留 chunk_overlap 个字符的重叠
   - 超长段落按 段落 → 换行 → 句号等标点 → 逗号 → 空格 → 硬截断 的顺序递归切成更小的单元
   - 超长的表格 / 代码块按行切分，每片都带上表头 / 代码围栏，保证单独拿出来也能看懂
4. chunk 不跨章节；向量化时用 embedding_text（文档标题 + 标题路径 + 正文），让孤立的块也带上下文

长度按字符数计（中文为主的文档里，字符数与 token 数大致成比例）。
"""
from dataclasses import dataclass, field

from app.core.config import settings
from app.models.document import Document
from app.rag.html_parser import Block, html_to_blocks

# 超长文本的切分点，按优先级排列；切分后分隔符保留在前一段末尾
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "!", "?", ";", ". ", "，", ",", " "]


@dataclass
class Chunk:
    index: int  # 在文档内的序号，从 0 开始，表明参考文献的具体位置可信度更高
    content: str  # 块正文
    heading_path: list[str] = field(default_factory=list)  # 所在章节的标题路径，从高层级到低层级
    doc_title: str = ""
    doc_id: int | None = None
    kb_id: int | None = None

    @property
    def embedding_text(self) -> str:
        """用于向量化 / 交给大模型的文本：带上文档标题和章节路径。"""
        head = " > ".join([self.doc_title, *self.heading_path] if self.doc_title else self.heading_path)
        return f"{head}\n\n{self.content}" if head else self.content


@dataclass
class _Unit:
    """装箱的最小单元：一个块或块的一部分。glue 是与前一个单元拼接时插入的字符。"""
    text: str
    glue: str = "\n"


class DocumentChunker:
    def __init__(self, chunk_size: int | None = None, chunk_overlap: int | None = None):
        self.chunk_size = chunk_size or settings.RAG_CHUNK_SIZE
        self.chunk_overlap = settings.RAG_CHUNK_OVERLAP if chunk_overlap is None else chunk_overlap
        if self.chunk_size <= 0:
            raise ValueError("chunk_size 必须大于 0")
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("chunk_overlap 必须在 [0, chunk_size) 范围内")

    # ---------- 对外接口 ----------

    def split_document(self, doc: Document) -> list[Chunk]:
        return self.split_html(doc.content, title=doc.title, doc_id=doc.id, kb_id=doc.kb_id)

    def split_html(self, html: str, title: str = "", doc_id: int | None = None, kb_id: int | None = None) -> list[Chunk]:
        return self.split_blocks(html_to_blocks(html), title=title, doc_id=doc_id, kb_id=kb_id)

    def split_blocks(
        self, blocks: list[Block], title: str = "", doc_id: int | None = None, kb_id: int | None = None
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        for path, body in self._sections(blocks):
            units = [u for b in body for u in self._units(b)]
            for content in self._merge(units):
                chunks.append(Chunk(len(chunks), content, list(path), title, doc_id, kb_id))
        return chunks

    # ---------- 按标题分章节 ----------

    @staticmethod
    def _sections(blocks: list[Block]) -> list[tuple[list[str], list[Block]]]:
        sections: list[tuple[list[str], list[Block]]] = []
        stack: list[tuple[int, str]] = []  # (标题层级, 标题文字)
        body: list[Block] = []

        def close():
            if body:
                sections.append(([t for _, t in stack], list(body)))
                body.clear()

        for b in blocks:
            if b.type == "heading":
                close()
                while stack and stack[-1][0] >= b.level:
                    stack.pop()
                stack.append((b.level, b.text))
            else:
                body.append(b)
        close()
        return sections

    # ---------- 块 → 不超过 chunk_size 的单元 ----------

    def _units(self, block: Block) -> list[_Unit]:
        if len(block.text) <= self.chunk_size:
            return [_Unit(block.text)]
        if block.type == "table":
            lines = block.text.split("\n")
            pieces = self._pack_lines(lines[2:], head="\n".join(lines[:2]) + "\n", tail="")
        elif block.type == "code":
            lines = block.text.split("\n")
            pieces = self._pack_lines(lines[1:-1], head=lines[0] + "\n", tail="\n```")
        else:
            return [_Unit(t, "\n" if i == 0 else "") for i, t in enumerate(self._split_text(block.text, _SEPARATORS))]
        return [_Unit(p) for p in pieces]

    def _split_text(self, text: str, separators: list[str]) -> list[str]:
        """递归切分，返回的每一段都不超过 chunk_size，按顺序拼接可还原原文。"""
        if len(text) <= self.chunk_size:
            return [text]
        for i, sep in enumerate(separators):
            if sep in text:
                parts = [p + sep for p in text.split(sep)]
                parts[-1] = parts[-1][: -len(sep)]
                return [s for p in parts if p for s in self._split_text(p, separators[i + 1:])]
        return [text[i: i + self.chunk_size] for i in range(0, len(text), self.chunk_size)]

    def _pack_lines(self, lines: list[str], head: str, tail: str) -> list[str]:
        """按行装箱，每片都加上 head / tail（表头或代码围栏）。"""
        room = max(self.chunk_size - len(head) - len(tail), 1)
        pieces: list[str] = []
        cur: list[str] = []
        cur_len = 0
        for line in lines:
            # 单行超长时硬截断
            for seg in [line[i: i + room] for i in range(0, len(line), room)] or [""]:
                if cur and cur_len + 1 + len(seg) > room:
                    pieces.append(head + "\n".join(cur) + tail)
                    cur, cur_len = [], 0
                cur_len += len(seg) + (1 if cur else 0)
                cur.append(seg)
        if cur:
            pieces.append(head + "\n".join(cur) + tail)
        return pieces

    # ---------- 单元 → chunk（贪心装箱 + 重叠） ----------

    def _merge(self, units: list[_Unit]) -> list[str]:
        chunks: list[str] = []
        cur: list[_Unit] = []

        def length(us: list[_Unit]) -> int:
            return sum(len(u.text) for u in us) + sum(len(u.glue) for u in us[1:])

        for unit in units:
            if cur and length(cur + [unit]) > self.chunk_size:
                chunks.append(self._join(cur))
                # 从上一个 chunk 末尾保留不超过 chunk_overlap 的单元作为重叠，且要给新单元留出位置
                while cur and (length(cur) > self.chunk_overlap or length(cur + [unit]) > self.chunk_size):
                    cur.pop(0)
            cur.append(unit)
        if cur:
            chunks.append(self._join(cur))
        return [c for c in chunks if c]

    @staticmethod
    def _join(units: list[_Unit]) -> str:
        return "".join(u.text if i == 0 else u.glue + u.text for i, u in enumerate(units)).strip()
