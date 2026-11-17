"""
Query Rewrite：检索前用大模型把用户的提问改写成适合检索的问题，提升模糊提问、追问的检索质量。

    rewritten = rewrite(client, "那 win 上咋弄", history)
    # RewrittenQuery(query="Windows 系统上如何安装？", keywords=["Windows", "安装步骤"])

- 补全指代和省略：结合对话历史把追问改写成独立完整的问题（比拼接上一个问题更准确，也不会把无关的历史带进检索）
- 澄清口语化、模糊的表达，纠正错别字、展开缩写；专有名词、型号、错误码等原样保留
- keywords：术语全称、同义词、常见别称，只用于关键词检索（BM25），补上用户没说出口的字面表达
- 改写失败（未开启、超时、接口报错、返回格式不对）时返回 None，由调用方回退到不改写的检索，不影响问答
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.rag.llm import ChatError, OpenAIChatClient

logger = logging.getLogger(__name__)

REWRITE_PROMPT = """你是企业知识库检索系统的查询改写助手。把用户最新的问题改写成一个适合在知识库中检索的、独立完整的问题。
规则：
1. 结合对话历史补全指代和省略，例如讨论安装时追问“那 Windows 呢？”，改写为“Windows 系统如何安装？”；与历史无关的新问题不要带入历史内容。
2. 把口语化、模糊的表达改成清晰具体的说法，纠正明显的错别字，展开缩写；专有名词、产品名、型号、错误码、版本号、命令等原样保留。
3. 不要回答问题，不要添加原问题和对话历史中没有的事实或限定条件；问题本身已经清晰时原样返回。
4. keywords 给出 0~5 个有助于检索的关键词：术语全称、同义词、常见别称等，不要简单重复问题中已有的词。
5. 使用与用户问题相同的语言。
只输出 JSON，不要输出其他内容：{"query": "改写后的问题", "keywords": ["关键词1", "关键词2"]}"""

MAX_KEYWORDS = 5
_KEYWORD_MAX_CHARS = 50
_JSON_OBJECT = re.compile(r"\{.*\}", re.S)  # 部分模型会用 ```json 代码块包裹，或在前后多说几句


@dataclass
class RewrittenQuery:
    query: str  # 改写后的问题，用于语义检索
    keywords: list[str] = field(default_factory=list)

    def keyword_text(self, question: str) -> str:
        """关键词检索的文本：原问题 + 改写后的问题 + 关键词。带上原问题，避免改写丢掉用户的原词。"""
        return " ".join(dict.fromkeys([question, self.query, *self.keywords]))


def build_messages(question: str, history: list[dict]) -> list[dict]:
    recent = history[-settings.QA_HISTORY_MESSAGES:] if settings.QA_HISTORY_MESSAGES > 0 else []
    width = settings.QA_REWRITE_HISTORY_CHARS
    lines = [
        f"{'用户' if m['role'] == 'user' else '助手'}：{m['content'] if m['role'] == 'user' else _shorten(m['content'], width)}"
        for m in recent
    ]
    context = "对话历史：\n" + "\n".join(lines) + "\n\n" if lines else ""
    return [
        {"role": "system", "content": REWRITE_PROMPT},
        {"role": "user", "content": f"{context}最新问题：{question}"},
    ]


def rewrite(client: OpenAIChatClient, question: str, history: list[dict]) -> RewrittenQuery | None:
    """改写问题；失败时返回 None。"""
    started = time.perf_counter()
    try:
        reply = client.complete(
            build_messages(question, history), model=settings.QA_REWRITE_MODEL, timeout=settings.QA_REWRITE_TIMEOUT
        )
        result = parse(reply)
    except (ChatError, ValueError) as e:
        logger.warning('问题改写失败，使用原问题检索 question="%s"：%s', question, e)
        return None
    logger.info(
        'Query Rewrite %dms："%s" → "%s" keywords=%s',
        (time.perf_counter() - started) * 1000, question, result.query, result.keywords,
    )
    return result


def parse(reply: str) -> RewrittenQuery:
    """解析大模型返回的 JSON，格式不对时抛出 ValueError。"""
    match = _JSON_OBJECT.search(reply or "")
    if not match:
        raise ValueError(f"返回内容不是 JSON：{(reply or '')[:200]}")
    data = json.loads(match.group())  # JSONDecodeError 是 ValueError 的子类
    query = data.get("query") if isinstance(data, dict) else None
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"返回内容缺少 query：{match.group()[:200]}")
    raw = data.get("keywords")
    keywords = [k.strip()[:_KEYWORD_MAX_CHARS] for k in raw if isinstance(k, str) and k.strip()] if isinstance(raw, list) else []
    return RewrittenQuery(
        query=" ".join(query.split())[: settings.RAG_QUESTION_MAX_LENGTH],
        keywords=list(dict.fromkeys(keywords))[:MAX_KEYWORDS],
    )


def _shorten(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if width <= 0 or len(text) <= width else text[:width] + "…"
