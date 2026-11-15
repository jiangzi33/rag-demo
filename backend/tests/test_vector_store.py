import socket
import uuid

import pytest

from app.core.config import settings
from app.rag.vector_store import MilvusVectorStore, VectorRecord


def _milvus_up() -> bool:
    host, _, port = settings.MILVUS_URI.removeprefix("http://").removeprefix("https://").partition(":")
    try:
        socket.create_connection((host, int(port or 19530)), timeout=1).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _milvus_up(), reason="本地 Milvus 未启动")


@pytest.fixture
def store():
    # 每个用例使用独立的临时集合，结束后删除，不影响正式数据
    s = MilvusVectorStore(uri=settings.MILVUS_URI, collection=f"test_rag_{uuid.uuid4().hex[:12]}", token=settings.MILVUS_TOKEN)
    yield s
    s._client.drop_collection(s.collection)
    s._client.close()


def rec(id, doc_id, kb_id, vector, idx=0):
    return VectorRecord(id, doc_id, kb_id, idx, "安装 > Linux", f"块{id}", vector)


def test_replace_search_delete(store):
    assert store.search([1.0, 0, 0], kb_ids=[1]) == []  # 集合还未创建

    store.replace_document(10, [rec(1, 10, 1, [1.0, 0, 0]), rec(2, 10, 1, [0, 1.0, 0], 1)])
    store.replace_document(20, [rec(3, 20, 2, [1.0, 0.1, 0])])

    hits = store.search([1.0, 0, 0], kb_ids=[1], top_k=5)
    assert [h.id for h in hits] == [1, 2]  # 只在知识库 1 内检索，按相似度排序
    assert hits[0].score == pytest.approx(1.0)
    assert (hits[0].doc_id, hits[0].heading, hits[0].content) == (10, "安装 > Linux", "块1")
    assert {h.id for h in store.search([1.0, 0, 0], kb_ids=[1, 2])} == {1, 2, 3}

    # 整篇替换：旧向量不残留
    store.replace_document(10, [rec(4, 10, 1, [0, 0, 1.0])])
    assert [h.id for h in store.search([1.0, 0, 0], kb_ids=[1])] == [4]

    store.delete_by_doc(10)
    assert store.search([1.0, 0, 0], kb_ids=[1]) == []
    store.delete_by_kb(2)
    assert store.search([1.0, 0, 0], kb_ids=[2]) == []


def test_dimension_mismatch(store):
    store.replace_document(1, [rec(1, 1, 1, [1.0, 0, 0])])
    other = MilvusVectorStore(uri=settings.MILVUS_URI, collection=store.collection, token=settings.MILVUS_TOKEN)
    with pytest.raises(ValueError, match="维度为 3"):
        other.replace_document(1, [rec(1, 1, 1, [1.0, 0])])


def test_keyword_search(store):
    """BM25 关键词检索：jieba 中文分词、英文不区分大小写、忽略虚词，按知识库 / 文档范围过滤。"""
    def doc(id, doc_id, kb_id, text):
        return VectorRecord(id, doc_id, kb_id, 0, "", text, [1.0, 0, 0], text=f"运维手册 > 故障\n\n{text}")

    store.replace_document(10, [doc(1, 10, 1, "错误码 E1024 表示磁盘已满，需要清理日志"), doc(2, 10, 1, "MySQL 的 InnoDB 存储引擎支持事务")])
    store.replace_document(20, [doc(3, 20, 2, "InnoDB 使用行级锁")])
    assert store.supports_keyword()

    hits = store.keyword_search("innodb 怎么支持事务", kb_ids=[1], top_k=5)
    assert [h.id for h in hits] == [2] and hits[0].score > 0 and hits[0].vector == pytest.approx([1.0, 0, 0])
    assert [h.id for h in store.keyword_search("e1024", kb_ids=[1, 2])] == [1]
    assert {h.id for h in store.keyword_search("InnoDB", kb_ids=[1], doc_ids=[20])} == {2, 3}
    assert {h.id for h in store.keyword_search("运维手册", kb_ids=[1])} == {1, 2}  # 文档标题也参与关键词检索
    assert store.keyword_search("怎么 的 是", kb_ids=[1, 2]) == []  # 只有虚词，不算命中
    assert store.keyword_search("   ", kb_ids=[1]) == [] and store.keyword_search("InnoDB", kb_ids=[]) == []


def test_old_collection_without_keyword_index(store):
    """旧版本创建的集合（没有 text / sparse 字段）：照常写入和语义检索，关键词检索返回空。"""
    from pymilvus import DataType, MilvusClient

    schema = MilvusClient.create_schema(auto_id=False)
    for f in ("id", "doc_id", "kb_id", "chunk_index"):
        schema.add_field(f, DataType.INT64, is_primary=f == "id")
    schema.add_field("heading", DataType.VARCHAR, max_length=1024)
    schema.add_field("content", DataType.VARCHAR, max_length=1024)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=3)
    index = MilvusClient.prepare_index_params()
    index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    store._client.create_collection(store.collection, schema=schema, index_params=index)

    store.replace_document(10, [rec(1, 10, 1, [1.0, 0, 0])])
    assert not store.supports_keyword()
    assert [h.id for h in store.search([1.0, 0, 0], kb_ids=[1])] == [1]
    assert store.keyword_search("块1", kb_ids=[1]) == []

    store.drop_collection()  # 重建后按新结构创建，支持关键词检索
    store.replace_document(10, [rec(1, 10, 1, [1.0, 0, 0])])
    assert store.supports_keyword()
