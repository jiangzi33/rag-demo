"""
RAG 第三步：把切分块的向量存入 Milvus，并按知识库做相似度检索（语义）和 BM25 全文检索（关键词）。

集合（collection）结构，每个切分块一条：
    id           INT64 主键 = document_chunks.id
    doc_id       INT64       所属文档，按文档整篇删除 / 重建
    kb_id        INT64       所属知识库，检索时按知识库过滤
    chunk_index  INT64       在文档内的序号
    heading      VARCHAR     标题路径，如 "安装 > Linux"
    content      VARCHAR     块正文（交给大模型的上下文）
    text         VARCHAR     关键词检索的文本（文档标题 + 标题路径 + 正文），jieba 分词
    vector       FLOAT_VECTOR(dim)  余弦相似度，AUTOINDEX
    sparse       SPARSE_FLOAT_VECTOR 由 Milvus 的 BM25 函数从 text 自动生成，写入时不用传

集合在第一次写入时按向量维度自动创建；换了维度不同的向量模型时会报错，需要删除集合后重新向量化。
旧版本创建的集合没有 text / sparse 字段，只能语义检索，执行 python -m scripts.reindex --recreate 重建后支持关键词检索。
"""
import logging
from dataclasses import dataclass
from functools import lru_cache

from pymilvus import DataType, Function, FunctionType, MilvusClient
from pymilvus.exceptions import MilvusException

from app.core.config import settings

logger = logging.getLogger(__name__)

_VARCHAR_MAX_BYTES = 65535

# 关键词检索的分词：jieba 中文分词 + 英文转小写 + 去掉常见虚词（否则"的""怎么"这类词也会算作命中）
# Milvus Lite 使用 jieba 分词需要在运行 Milvus 的 Python 环境中安装 jieba
STOP_WORDS = [
    "的", "了", "和", "与", "及", "或", "是", "在", "有", "也", "就", "都", "而", "被", "把", "对", "从", "到", "为",
    "这", "那", "这个", "那个", "这些", "那些", "一个", "我", "你", "他", "她", "它", "我们", "你们", "他们",
    "吗", "呢", "吧", "啊", "呀", "么", "怎么", "怎么样", "怎样", "如何", "什么", "哪些", "哪个", "为什么", "请问", "可以", "能", "会",
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "is", "are", "be", "how", "what", "which", "why", "do", "does",
]
ANALYZER_PARAMS = {"tokenizer": "jieba", "filter": ["lowercase", {"type": "stop", "stop_words": STOP_WORDS}]}

# pymilvus 默认每 10 秒发一次 keepalive ping，空闲时也发；Milvus 服务端认为 ping 过于频繁，
# 会断开连接并打印 GOAWAY too_many_pings。改为 60 秒一次，且空闲时不发
GRPC_OPTIONS = {"grpc.keepalive_time_ms": 60000, "grpc.keepalive_permit_without_calls": False}


@dataclass
class VectorRecord:
    id: int  # document_chunks.id
    doc_id: int
    kb_id: int
    chunk_index: int
    heading: str
    content: str
    vector: list[float]
    text: str = ""  # 关键词检索的文本，不填时用 content


@dataclass
class SearchHit:
    id: int
    doc_id: int
    kb_id: int
    chunk_index: int
    heading: str
    content: str
    score: float  # search：余弦相似度；keyword_search：BM25 得分。都是越大越相关
    vector: list[float] | None = None  # keyword_search 带回片段向量，用于计算与问题的余弦相似度


def _truncate_bytes(s: str, max_bytes: int = _VARCHAR_MAX_BYTES) -> str:
    """Milvus 的 VARCHAR 按 UTF-8 字节数限制长度。"""
    b = s.encode("utf-8")
    return s if len(b) <= max_bytes else b[:max_bytes].decode("utf-8", errors="ignore")


class MilvusVectorStore:
    OUTPUT_FIELDS = ["doc_id", "kb_id", "chunk_index", "heading", "content"]

    def __init__(self, uri: str, collection: str, token: str = "", client: MilvusClient | None = None):
        self.collection = collection
        self._client = client or MilvusClient(uri=uri, token=token, grpc_options=GRPC_OPTIONS)
        self._ready = False
        self._loaded = False
        self._dim: int | None = None
        self._keyword: bool | None = None

    # ---------- 写入 ----------

    def replace_document(self, doc_id: int, records: list[VectorRecord]) -> None:
        """整篇替换：先删除该文档原有的向量，再写入新的，与 document_chunks 的整篇重建保持一致。"""
        if records:
            self._ensure_collection(len(records[0].vector))
        elif not self._exists():
            return
        self._client.delete(self.collection, filter=f"doc_id == {int(doc_id)}")
        if records:
            self._client.insert(self.collection, [self._to_row(r) for r in records])

    def delete_by_doc(self, doc_id: int) -> None:
        if self._exists():
            self._client.delete(self.collection, filter=f"doc_id == {int(doc_id)}")

    def delete_by_kb(self, kb_id: int) -> None:
        if self._exists():
            self._client.delete(self.collection, filter=f"kb_id == {int(kb_id)}")

    # ---------- 检索 ----------

    def search(
        self, vector: list[float], kb_ids: list[int], top_k: int = 5, doc_ids: list[int] | None = None
    ) -> list[SearchHit]:
        """语义检索：在限定范围内检索最相似的 top_k 个切分块，按相似度从高到低排序。
        范围 = kb_ids 中的整个知识库 + doc_ids 中的单篇文档（用户只有文档授权时），两者都为空时不检索。"""
        scope = self._scope_filter(kb_ids, doc_ids)
        if not scope or not self._exists():
            return []
        results = self._search(data=[vector], anns_field="vector", filter=scope, limit=top_k, output_fields=self.OUTPUT_FIELDS)
        return [SearchHit(id=hit["id"], score=hit["distance"], **{f: hit["entity"][f] for f in self.OUTPUT_FIELDS})
                for hit in results[0]]

    def keyword_search(
        self, text: str, kb_ids: list[int], top_k: int = 5, doc_ids: list[int] | None = None
    ) -> list[SearchHit]:
        """关键词检索（BM25）：返回包含问题中关键词的 top_k 个切分块，按 BM25 得分从高到低排序，范围同 search。
        集合不支持关键词检索（旧版本创建）时返回空列表。"""
        scope = self._scope_filter(kb_ids, doc_ids)
        if not scope or not text.strip() or not self.supports_keyword():
            return []
        results = self._search(
            data=[text], anns_field="sparse", filter=scope, limit=top_k, output_fields=[*self.OUTPUT_FIELDS, "vector"]
        )
        # Milvus Lite 返回的 BM25 距离为负数（越小越相关），Milvus 服务端为正数，统一取绝对值
        return [SearchHit(id=hit["id"], score=abs(hit["distance"]), vector=list(hit["entity"]["vector"]),
                          **{f: hit["entity"][f] for f in self.OUTPUT_FIELDS})
                for hit in results[0]]

    @staticmethod
    def _scope_filter(kb_ids: list[int], doc_ids: list[int] | None) -> str:
        conditions = []
        if kb_ids:
            conditions.append(f"kb_id in {[int(k) for k in kb_ids]}")
        if doc_ids:
            conditions.append(f"doc_id in {[int(d) for d in doc_ids]}")
        return " or ".join(conditions)

    def _search(self, **kwargs):
        """检索前确保集合已加载到内存。Milvus 重启后集合会变为 released 状态，
        此时检索报 "call load() before search"，重新加载后重试一次。"""
        self._ensure_loaded()
        try:
            return self._client.search(self.collection, **kwargs)
        except MilvusException as e:
            if "released" not in str(e.message) and "not loaded" not in str(e.message):
                raise
            logger.warning("Milvus 集合 %s 未加载，重新加载后重试检索", self.collection)
            self._loaded = False
            self._ensure_loaded()
            return self._client.search(self.collection, **kwargs)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._client.load_collection(self.collection)
            self._loaded = True

    # ---------- 集合管理 ----------

    def dimension(self) -> int | None:
        """集合的向量维度；集合还不存在（还没有文档完成向量化）时返回 None。"""
        if self._dim is None and self._client.has_collection(self.collection):
            self._dim = self._read_dimension()
        return self._dim

    def supports_keyword(self) -> bool:
        """集合是否支持关键词检索。集合不存在时返回 False（之后创建的集合都支持）。"""
        if self._keyword is None:
            if not self._client.has_collection(self.collection):
                return False
            fields = {f["name"] for f in self._client.describe_collection(self.collection)["fields"]}
            self._keyword = {"text", "sparse"} <= fields
            if not self._keyword:
                logger.warning(
                    "Milvus 集合 %s 是旧版本创建的，不支持关键词检索，只使用语义检索；"
                    "执行 python -m scripts.reindex --recreate 重建集合", self.collection,
                )
        return self._keyword

    def drop_collection(self) -> None:
        """删除整个集合（重建索引用），之后第一次写入时按新结构重新创建。"""
        if self._client.has_collection(self.collection):
            self._client.drop_collection(self.collection)
        self._ready, self._loaded, self._dim, self._keyword = False, False, None, None

    def _exists(self) -> bool:
        return self._ready or self._client.has_collection(self.collection)

    def _ensure_collection(self, dim: int) -> None:
        if self._ready:
            return
        existing = self.dimension()
        if existing is not None:
            if existing != dim:
                raise ValueError(
                    f"Milvus 集合 {self.collection} 的向量维度为 {existing}，当前向量模型输出 {dim} 维；"
                    "更换模型后请删除该集合并重新向量化"
                )
        else:
            self._create_collection(dim)
            logger.info("已创建 Milvus 集合 %s（%d 维）", self.collection, dim)
        self._ensure_loaded()
        self._dim = dim
        self._ready = True

    def _read_dimension(self) -> int | None:
        for f in self._client.describe_collection(self.collection)["fields"]:
            if f["name"] == "vector":
                return int(f["params"]["dim"])
        return None

    def _create_collection(self, dim: int) -> None:
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("doc_id", DataType.INT64)
        schema.add_field("kb_id", DataType.INT64)
        schema.add_field("chunk_index", DataType.INT64)
        schema.add_field("heading", DataType.VARCHAR, max_length=_VARCHAR_MAX_BYTES)
        schema.add_field("content", DataType.VARCHAR, max_length=_VARCHAR_MAX_BYTES)
        schema.add_field(
            "text", DataType.VARCHAR, max_length=_VARCHAR_MAX_BYTES, enable_analyzer=True, analyzer_params=ANALYZER_PARAMS
        )
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(
            Function(name="text_bm25", function_type=FunctionType.BM25, input_field_names=["text"], output_field_names=["sparse"])
        )

        index_params = MilvusClient.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        index_params.add_index(field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
        # 标量索引：按文档删除、按知识库过滤
        index_params.add_index(field_name="doc_id", index_type="INVERTED")
        index_params.add_index(field_name="kb_id", index_type="INVERTED")
        self._client.create_collection(self.collection, schema=schema, index_params=index_params)
        self._keyword = True

    def _to_row(self, r: VectorRecord) -> dict:
        row = {
            "id": r.id,
            "doc_id": r.doc_id,
            "kb_id": r.kb_id,
            "chunk_index": r.chunk_index,
            "heading": _truncate_bytes(r.heading),
            "content": _truncate_bytes(r.content),
            "vector": r.vector,
        }
        if self.supports_keyword():
            row["text"] = _truncate_bytes(r.text or r.content)
        return row


@lru_cache
def get_vector_store() -> MilvusVectorStore:
    """第一次使用时才连接 Milvus，Milvus 未启动不影响应用启动。"""
    return MilvusVectorStore(uri=settings.MILVUS_URI, collection=settings.MILVUS_COLLECTION, token=settings.MILVUS_TOKEN)
