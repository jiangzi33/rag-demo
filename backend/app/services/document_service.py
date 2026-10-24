import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import Connection

from app.core.config import settings
from app.core.exceptions import BizError, ErrorCode
from app.mapper import document_mapper
from app.mapper import knowledge_base_mapper as kb_mapper
from app.mapper import user_mapper
from app.models.document import Document
from app.models.user import User
from app.schemas.document import DocumentBriefOut, DocumentIn, DocumentOut
from app.schemas.user import UserBriefOut
from app.services import knowledge_base_service as kb_service


def _authors(conn: Connection, author_ids: set[int]) -> dict[int, UserBriefOut]:
    """一次批量查询补齐作者信息，避免 N+1。"""
    return {u.id: UserBriefOut.model_validate(u) for u in user_mapper.select_by_ids(conn, list(author_ids))}


def to_out(conn: Connection, doc: Document) -> DocumentOut:
    author = _authors(conn, {doc.author_id}).get(doc.author_id)
    return DocumentOut(**asdict(doc), author=author)


def get_or_404(conn: Connection, user: User, kb_id: int, doc_id: int) -> Document:
    """先校验知识库权限，再校验文档确实属于该知识库。"""
    kb_service.get_managed_or_404(conn, user, kb_id)
    doc = document_mapper.select_by_id(conn, doc_id)
    if not doc or doc.kb_id != kb_id:
        raise BizError("文档不存在", ErrorCode.NOT_FOUND, 404)
    return doc


def list_by_kb(
    conn: Connection, user: User, kb_id: int, page: int, size: int, keyword: str | None
) -> tuple[list[DocumentBriefOut], int]:
    kb_service.get_managed_or_404(conn, user, kb_id)
    total = document_mapper.count_by_kb(conn, kb_id, keyword)
    if not total:
        return [], 0
    rows = document_mapper.select_page_by_kb(conn, kb_id, keyword, offset=(page - 1) * size, limit=size)
    authors = _authors(conn, {r["author_id"] for r in rows})
    return [DocumentBriefOut(**r, author=authors.get(r["author_id"])) for r in rows], total


def create(conn: Connection, user: User, kb_id: int, data: DocumentIn) -> Document:
    kb_service.get_managed_or_404(conn, user, kb_id)
    doc_id = document_mapper.insert(conn, kb_id=kb_id, title=data.title, content=data.content, author_id=user.id)
    kb_mapper.touch(conn, kb_id)
    conn.commit()
    return document_mapper.select_by_id(conn, doc_id)


def update(conn: Connection, user: User, kb_id: int, doc_id: int, data: DocumentIn) -> Document:
    doc = get_or_404(conn, user, kb_id, doc_id)
    if data.title == doc.title and data.content == doc.content:
        return doc  # 内容没变，不刷新最近编辑日期
    document_mapper.update_content(conn, doc.id, data.title, data.content)
    kb_mapper.touch(conn, kb_id)
    conn.commit()
    return document_mapper.select_by_id(conn, doc.id)


def delete(conn: Connection, user: User, kb_id: int, doc_id: int) -> None:
    doc = get_or_404(conn, user, kb_id, doc_id)
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
    kb_service.get_managed_or_404(conn, user, kb_id)
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
