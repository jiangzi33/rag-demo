"""
重建 Milvus 中全部文档的向量和关键词索引（在 backend 目录下执行）：

    python -m scripts.reindex               # 重新向量化全部文档，按文档整篇替换
    python -m scripts.reindex --recreate    # 先删除整个集合再重建：升级到支持关键词检索的新结构、更换向量模型时使用
    python -m scripts.reindex --rechunk     # 同时按文档当前正文重新切分（编辑过的文档、切分功能上线前的旧文档）

数据都来自 MySQL 中的文档和切分块，重建不会丢失内容；重建期间检索结果不完整。
"""
import argparse
import logging
import sys
from pathlib import Path

# 也支持在任意目录下直接运行：python backend/scripts/reindex.py --recreate
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.mapper import document_mapper  # noqa: E402
from app.rag.vector_store import get_vector_store  # noqa: E402
from app.services import chunk_service, embedding_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="重建 Milvus 向量和关键词索引")
    parser.add_argument("--recreate", action="store_true", help="先删除整个集合，再按新结构重建")
    parser.add_argument("--rechunk", action="store_true", help="同时按文档当前正文重新切分")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    store = get_vector_store()
    if args.recreate:
        store.drop_collection()
        print(f"已删除集合 {settings.MILVUS_COLLECTION}")

    with engine.connect() as conn:
        doc_ids = [r[0] for r in conn.execute(text("SELECT id FROM documents ORDER BY id"))]
    failed = 0
    for i, doc_id in enumerate(doc_ids, 1):
        try:
            with engine.connect() as conn:
                if args.rechunk:
                    chunk_service.rebuild(conn, document_mapper.select_by_id(conn, doc_id))
                    conn.commit()
                doc, items = embedding_service.load_and_embed(conn, doc_id)
            if items:
                embedding_service.save_vectors(doc, items)
            else:
                store.delete_by_doc(doc_id)
            print(f"[{i}/{len(doc_ids)}] 文档 {doc_id}《{doc.title if doc else ''}》：{len(items)} 个切分块")
        except Exception as e:
            failed += 1
            print(f"[{i}/{len(doc_ids)}] 文档 {doc_id} 失败：{e}", file=sys.stderr)
    print(f"完成：{len(doc_ids) - failed} 篇成功，{failed} 篇失败；关键词检索{'已' if store.supports_keyword() else '未'}启用")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
