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
