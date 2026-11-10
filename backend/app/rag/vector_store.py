"""
RAG 第三步：把切分块的向量存入 Milvus，并按知识库做相似度检索。

集合（collection）结构，每个切分块一条：
    id           INT64 主键 = document_chunks.id
    doc_id       INT64       所属文档，按文档整篇删除 / 重建
    kb_id        INT64       所属知识库，检索时按知识库过滤
    chunk_index  INT64       在文档内的序号
    heading      VARCHAR     标题路径，如 "安装 > Linux"
    content      VARCHAR     块正文（交给大模型的上下文）
    vector       FLOAT_VECTOR(dim)  余弦相似度，AUTOINDEX

集合在第一次写入时按向量维度自动创建；换了维度不同的向量模型时会报错，需要删除集合后重新向量化。
"""
import logging
from dataclasses import dataclass
from functools import lru_cache

from pymilvus import DataType, MilvusClient

from app.core.config import settings

logger = logging.getLogger(__name__)

_VARCHAR_MAX_BYTES = 65535


@dataclass
class VectorRecord:
    id: int  # document_chunks.id
    doc_id: int
    kb_id: int
    chunk_index: int
    heading: str
    content: str
    vector: list[float]


@dataclass
class SearchHit:
    id: int
    doc_id: int
    kb_id: int
    chunk_index: int
    heading: str
    content: str
    score: float  # 余弦相似度，越大越相关


def _truncate_bytes(s: str, max_bytes: int = _VARCHAR_MAX_BYTES) -> str:
    """Milvus 的 VARCHAR 按 UTF-8 字节数限制长度。"""
    b = s.encode("utf-8")
    return s if len(b) <= max_bytes else b[:max_bytes].decode("utf-8", errors="ignore")


class MilvusVectorStore:
    OUTPUT_FIELDS = ["doc_id", "kb_id", "chunk_index", "heading", "content"]

    def __init__(self, uri: str, collection: str, token: str = "", client: MilvusClient | None = None):
        self.collection = collection
        self._client = client or MilvusClient(uri=uri, token=token)
        self._ready = False
        self._dim: int | None = None

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
        """在限定范围内检索最相似的 top_k 个切分块，按相似度从高到低排序。
        范围 = kb_ids 中的整个知识库 + doc_ids 中的单篇文档（用户只有文档授权时），两者都为空时不检索。"""
        conditions = []
        if kb_ids:
            conditions.append(f"kb_id in {[int(k) for k in kb_ids]}")
        if doc_ids:
            conditions.append(f"doc_id in {[int(d) for d in doc_ids]}")
        if not conditions or not self._exists():
            return []
        results = self._client.search(
            self.collection,
            data=[vector],
            filter=" or ".join(conditions),
            limit=top_k,
            output_fields=self.OUTPUT_FIELDS,
        )
        return [SearchHit(id=hit["id"], score=hit["distance"], **{f: hit["entity"][f] for f in self.OUTPUT_FIELDS})
                for hit in results[0]]

    # ---------- 集合管理 ----------

    def dimension(self) -> int | None:
        """集合的向量维度；集合还不存在（还没有文档完成向量化）时返回 None。"""
        if self._dim is None and self._client.has_collection(self.collection):
            self._dim = self._read_dimension()
        return self._dim

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
        self._client.load_collection(self.collection)
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
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)

        index_params = MilvusClient.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        # 标量索引：按文档删除、按知识库过滤
        index_params.add_index(field_name="doc_id", index_type="INVERTED")
        index_params.add_index(field_name="kb_id", index_type="INVERTED")
        self._client.create_collection(self.collection, schema=schema, index_params=index_params)

    @staticmethod
    def _to_row(r: VectorRecord) -> dict:
        return {
            "id": r.id,
            "doc_id": r.doc_id,
            "kb_id": r.kb_id,
            "chunk_index": r.chunk_index,
            "heading": _truncate_bytes(r.heading),
            "content": _truncate_bytes(r.content),
            "vector": r.vector,
        }


@lru_cache
def get_vector_store() -> MilvusVectorStore:
    """第一次使用时才连接 Milvus，Milvus 未启动不影响应用启动。"""
    return MilvusVectorStore(uri=settings.MILVUS_URI, collection=settings.MILVUS_COLLECTION, token=settings.MILVUS_TOKEN)
