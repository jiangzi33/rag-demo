from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile
from sqlalchemy import Connection

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.document import DocumentBriefOut, DocumentIn, DocumentOut, ImageOut
from app.schemas.user import Page, Resp
from app.services import document_service as doc_service
from app.services import embedding_service

router = APIRouter(prefix="/knowledge-bases/{kb_id}/documents", tags=["文档"])


@router.post("", response_model=Resp[DocumentOut], summary="新建文档（需要知识库写权限，当前用户为作者）")
def create(
    kb_id: int,
    body: DocumentIn,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    doc, level = doc_service.create(db, user, kb_id, body)
    # 响应返回后在后台把切分块转换成向量
    background_tasks.add_task(embedding_service.embed_document, doc.id)
    return Resp(data=doc_service.to_out(db, user, doc, level))


@router.get("", response_model=Resp[Page[DocumentBriefOut]], summary="知识库内的全部文档（不含正文，所有人可见）")
def list_docs(
    kb_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64, description="匹配文档标题"),
    user: User = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    items, total = doc_service.list_by_kb(db, user, kb_id, page, size, keyword)
    return Resp(data=Page(items=items, total=total, page=page, size=size))


@router.post("/images", response_model=Resp[ImageOut], summary="上传正文图片（png/jpg/gif/webp，≤10MB）")
async def upload_image(
    kb_id: int, file: UploadFile = File(...), user: User = Depends(get_current_user), db: Connection = Depends(get_db)
):
    url = doc_service.save_image(db, user, kb_id, await file.read())
    return Resp(data=ImageOut(url=url))


@router.get("/{doc_id}", response_model=Resp[DocumentOut], summary="文档详情（含正文，需要读权限）")
def detail(kb_id: int, doc_id: int, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    doc, level = doc_service.get_readable(db, user, kb_id, doc_id)
    return Resp(data=doc_service.to_out(db, user, doc, level))


@router.put("/{doc_id}", response_model=Resp[DocumentOut], summary="编辑文档标题和正文（需要写权限）")
def update(
    kb_id: int, doc_id: int, body: DocumentIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)
):
    doc, level = doc_service.update(db, user, kb_id, doc_id, body)
    return Resp(data=doc_service.to_out(db, user, doc, level))


@router.delete("/{doc_id}", response_model=Resp[None], summary="删除文档（需要管理权限）")
def delete(
    kb_id: int,
    doc_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    doc_service.delete(db, user, kb_id, doc_id)
    background_tasks.add_task(embedding_service.remove_document, doc_id)
    return Resp(message="删除成功")
