import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import Connection

from app.core.config import settings
from app.core.exceptions import BizError, ErrorCode
from app.mapper import document_chunk_mapper as chunk_mapper
from app.mapper import document_mapper
from app.mapper import knowledge_base_mapper as kb_mapper
from app.mapper import invite_link_mapper, permission_mapper, user_mapper
from app.models.document import Document
from app.models.permission import TargetType
from app.models.user import User
from app.schemas.document import DocumentBriefOut, DocumentIn, DocumentOut
from app.schemas.user import UserBriefOut
from app.services import access_service as access
from app.services import chunk_service
from app.services.access_service import Level


def _authors(conn: Connection, author_ids: set[int]) -> dict[int, UserBriefOut]:
    """一次批量查询补齐作者信息，避免 N+1。"""
    return {u.id: UserBriefOut.model_validate(u) for u in user_mapper.select_by_ids(conn, list(author_ids))}


def _pending(conn: Connection, user: User, doc_ids: list[int]) -> dict[int, str]:
    pending = permission_mapper.select_pending_application_types(conn, user.id, TargetType.DOC, doc_ids)
    return {doc_id: t.value for doc_id, t in pending.items()}


def to_out(conn: Connection, user: User, doc: Document, level: Level) -> DocumentOut:
    author = _authors(conn, {doc.author_id}).get(doc.author_id)
    pending = _pending(conn, user, [doc.id]).get(doc.id)
    return DocumentOut(**asdict(doc), author=author, my_permission=level.label, pending_permission=pending)


def get_readable(conn: Connection, user: User, kb_id: int, doc_id: int) -> tuple[Document, Level]:
    return access.require_doc(conn, user, kb_id, doc_id, Level.READ)


def list_by_kb(
    conn: Connection, user: User, kb_id: int, page: int, size: int, keyword: str | None
) -> tuple[list[DocumentBriefOut], int]:
    """所有人都能看到知识库中的全部文档（不含正文），my_permission 标明当前用户能否打开 / 编辑 / 管理。"""
    _, kb_lvl = access.require_kb(conn, user, kb_id, Level.NONE)
    total = document_mapper.count_by_kb(conn, kb_id, keyword)
    if not total:
        return [], 0
    rows = document_mapper.select_page_by_kb(conn, kb_id, keyword, offset=(page - 1) * size, limit=size)
    authors = _authors(conn, {r["author_id"] for r in rows})
    doc_ids = [r["id"] for r in rows]
    levels = access.doc_levels(conn, user, doc_ids, kb_lvl)
    pending = _pending(conn, user, doc_ids)
    return [
        DocumentBriefOut(
            **r, author=authors.get(r["author_id"]), my_permission=levels[r["id"]].label, pending_permission=pending.get(r["id"])
        )
        for r in rows
    ], total


def create(conn: Connection, user: User, kb_id: int, data: DocumentIn) -> tuple[Document, Level]:
    """在知识库中新建文档需要对知识库有写权限。"""
    _, kb_lvl = access.require_kb(conn, user, kb_id, Level.WRITE)
    doc_id = document_mapper.insert(conn, kb_id=kb_id, title=data.title, content=data.content, author_id=user.id)
    doc = document_mapper.select_by_id(conn, doc_id)
    chunk_service.rebuild(conn, doc)  # 与文档同一事务写入切分块，供 RAG 检索
    kb_mapper.touch(conn, kb_id)
    conn.commit()
    return doc, kb_lvl


def update(conn: Connection, user: User, kb_id: int, doc_id: int, data: DocumentIn) -> tuple[Document, Level]:
    doc, lvl = access.require_doc(conn, user, kb_id, doc_id, Level.WRITE)
    if data.title == doc.title and data.content == doc.content:
        return doc, lvl  # 内容没变，不刷新最近编辑日期
    document_mapper.update_content(conn, doc.id, data.title, data.content)
    doc = document_mapper.select_by_id(conn, doc.id)
    chunk_service.rebuild(conn, doc)  # 标题也参与切分块的上下文，所以只改标题也要重新切分
    kb_mapper.touch(conn, kb_id)
    conn.commit()
    return doc, lvl


def delete(conn: Connection, user: User, kb_id: int, doc_id: int) -> None:
    """删除文档：需要文档（或所在知识库）的管理权限。"""
    doc, _ = access.require_doc(conn, user, kb_id, doc_id, Level.MANAGE)
    permission_mapper.delete_by_target(conn, TargetType.DOC, doc.id)
    invite_link_mapper.delete_by_target(conn, TargetType.DOC, doc.id)
    chunk_mapper.delete_by_doc_id(conn, doc.id)
    document_mapper.delete_by_id(conn, doc.id)
    kb_mapper.touch(conn, kb_id)
    conn.commit()


# ---------- 正文图片 ----------
# 图片同样归属于知识库：存放在 uploads/doc-images/{kb_id}/ 下，删除知识库时整个目录一起删除

DOC_IMAGE_URL_PREFIX = "/uploads/doc-images/"
# 按文件头识别真实格式，不信任客户端声明的 Content-Type
_IMAGE_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}


def _image_ext(content: bytes) -> str | None:
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return next((ext for sig, ext in _IMAGE_SIGNATURES.items() if content.startswith(sig)), None)


def kb_image_dir(kb_id: int) -> Path:
    return settings.UPLOAD_DIR / "doc-images" / str(int(kb_id))


def save_image(conn: Connection, user: User, kb_id: int, content: bytes) -> str:
    if not access.can_write_in_kb(conn, user, kb_id):
        raise BizError("没有权限上传图片", ErrorCode.FORBIDDEN, 403)
    if len(content) > settings.DOC_IMAGE_MAX_BYTES:
        raise BizError(f"图片不能超过 {settings.DOC_IMAGE_MAX_BYTES // 1024 // 1024}MB", ErrorCode.FILE_INVALID)
    ext = _image_ext(content)
    if not ext:
        raise BizError("仅支持 png / jpg / gif / webp 格式的图片", ErrorCode.FILE_INVALID)

    directory = kb_image_dir(kb_id)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    (directory / filename).write_bytes(content)
    return f"{DOC_IMAGE_URL_PREFIX}{int(kb_id)}/{filename}"


def remove_kb_images(kb_id: int) -> None:
    shutil.rmtree(kb_image_dir(kb_id), ignore_errors=True)
