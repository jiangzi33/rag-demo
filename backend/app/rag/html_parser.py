"""
把文档正文（wangEditor 产生的富文本 HTML）解析成按顺序排列的结构化块，供切分使用。

- 标题 h1~h6 → heading 块，保留层级，用于生成块的标题路径
- 段落 / 列表项 / 引用 → 纯文本，列表加 "- " 或 "1. " 前缀，引用加 "> " 前缀，待办加 "[ ] " / "[x] "
- 代码块 → 保留原始换行和缩进，用 ``` 包裹并标注语言
- 表格 → 转成 Markdown 表格，第一行视为表头
- 图片只保留 alt 文本，样式等其余信息全部丢弃
"""
import re
from dataclasses import dataclass
from html.parser import HTMLParser

_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# 开始 / 结束时都要把已收集的文字作为一个块输出的标签
_BLOCK_TAGS = {"p", "div", "li", "hr"} | _HEADINGS
_SKIP_TAGS = {"script", "style"}
_WHITESPACE = re.compile(r"[ \t\r\f\v 　]+")


@dataclass
class Block:
    type: str  # heading / paragraph / list_item / quote / code / table
    text: str  # 已渲染好的文本（含列表符号、代码围栏、Markdown 表格等）
    level: int = 0  # 仅 heading 有效：1~6


def _normalize(text: str) -> str:
    """折叠空白、去掉空行（代码块不走这里）。"""
    lines = (_WHITESPACE.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


class _BlockParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self._buf: list[str] = []
        self._heading = 0
        self._list_prefix = ""  # 当前列表项的符号，只加在列表项输出的第一段文字前
        self._lists: list[list] = []  # 嵌套列表栈，每层 [是否有序, 当前序号]
        self._quote_depth = 0
        self._pre_depth = 0
        self._code_lang = ""
        self._skip_depth = 0
        # 表格：嵌套表格按外层表格的单元格文字处理
        self._table_depth = 0
        self._rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    # ---------- 输出 ----------

    def _flush(self):
        raw = "".join(self._buf)
        self._buf = []
        if self._pre_depth:
            text = raw.strip("\n")
            if text.strip():
                self.blocks.append(Block("code", f"```{self._code_lang}\n{text}\n```"))
            return
        text = _normalize(raw)
        if not text:
            return
        if self._heading:
            self.blocks.append(Block("heading", text.replace("\n", " "), self._heading))
        elif self._lists:
            indent = "  " * (len(self._lists) - 1)
            self.blocks.append(Block("list_item", f"{indent}{self._list_prefix}{text}"))
            self._list_prefix = ""
        elif self._quote_depth:
            self.blocks.append(Block("quote", "\n".join(f"> {line}" for line in text.split("\n"))))
        else:
            self.blocks.append(Block("paragraph", text))

    def _flush_table(self):
        rows = [r for r in self._rows if any(r)]
        self._rows = []
        if not rows:
            return
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        lines = ["| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in rows]
        lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
        self.blocks.append(Block("table", "\n".join(lines)))

    def _text(self, s: str):
        """写入当前位置：表格单元格内或普通文本缓冲区。"""
        if self._table_depth:
            if self._cell is not None:
                self._cell.append(s)
        else:
            self._buf.append(s)

    # ---------- HTMLParser 回调 ----------

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "table":
            if not self._table_depth:
                self._flush()
            self._table_depth += 1
            return
        if self._table_depth:
            if tag == "tr" and self._table_depth == 1:
                self._row = []
            elif tag in ("td", "th") and self._table_depth == 1:
                self._cell = []
            elif tag in ("br", "p", "div"):
                self._text(" ")
            elif tag == "img" and attrs.get("alt"):
                self._text(f"[图片: {attrs['alt']}]")
            return

        if tag in _BLOCK_TAGS or tag in ("blockquote", "pre", "ul", "ol"):
            self._flush()
        if tag in _HEADINGS:
            self._heading = int(tag[1])
        elif tag == "blockquote":
            self._quote_depth += 1
        elif tag == "pre":
            self._pre_depth += 1
            self._code_lang = ""
        elif tag == "code" and self._pre_depth:
            # wangEditor: <pre><code class="language-python">
            cls = attrs.get("class") or ""
            self._code_lang = cls.removeprefix("language-") if cls.startswith("language-") else ""
        elif tag in ("ul", "ol"):
            self._lists.append([tag == "ol", 0])
        elif tag == "li" and self._lists:
            current = self._lists[-1]
            current[1] += 1
            self._list_prefix = f"{current[1]}. " if current[0] else "- "
        elif tag == "br":
            self._text("\n")
        elif tag == "img" and attrs.get("alt"):
            self._text(f"[图片: {attrs['alt']}]")
        elif tag == "input" and attrs.get("type") == "checkbox":
            self._text("[x] " if "checked" in attrs else "[ ] ")

    def handle_startendtag(self, tag, attrs):
        # <br/>、<img/> 等自闭合标签只处理开始，不触发结束逻辑
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._table_depth:
            if tag == "table":
                self._table_depth -= 1
                if not self._table_depth:
                    self._flush_table()
            elif tag in ("td", "th") and self._table_depth == 1 and self._cell is not None:
                if self._row is not None:
                    self._row.append(_normalize("".join(self._cell)).replace("\n", " "))
                self._cell = None
            elif tag == "tr" and self._table_depth == 1 and self._row is not None:
                self._rows.append(self._row)
                self._row = None
            return

        if tag in _BLOCK_TAGS or tag in ("blockquote", "pre", "ul", "ol"):
            self._flush()
        if tag in _HEADINGS:
            self._heading = 0
        elif tag == "blockquote":
            self._quote_depth = max(0, self._quote_depth - 1)
        elif tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
        elif tag in ("ul", "ol") and self._lists:
            self._lists.pop()
        elif tag == "li":
            self._list_prefix = ""

    def handle_data(self, data):
        if not self._skip_depth:
            self._text(data)

    def close(self):
        super().close()
        if self._table_depth:  # 未闭合的表格
            self._table_depth = 0
            self._flush_table()
        self._flush()


def html_to_blocks(html: str) -> list[Block]:
    parser = _BlockParser()
    parser.feed(html or "")
    parser.close()
    return parser.blocks
