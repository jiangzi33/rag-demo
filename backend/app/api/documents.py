from functools import partial

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import Connection

from app.api.deps import get_current_user
from app.core.exceptions import BizError, ErrorCode
from app.db.session import get_db
from app.models.user import User
from app.rag.llm import ChatError, get_chat_client
from app.schemas.document import ChunkOut, DocumentBriefOut, DocumentIn, DocumentOut, ImageOut, SummaryIn
from app.schemas.user import Page, Resp
from app.services import document_service as doc_service
from app.services import embedding_service, summary_service

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


@router.get("/{doc_id}/chunks/{chunk_id}", response_model=Resp[ChunkOut], summary="文档的一个切分块（检索结果定位用，需要读权限）")
def chunk(kb_id: int, doc_id: int, chunk_id: int, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    return Resp(data=ChunkOut.model_validate(doc_service.get_chunk(db, user, kb_id, doc_id, chunk_id)))


@router.post("/{doc_id}/summary", summary="文档摘要：基于切分块由大模型生成，标注出处片段（需要读权限，text/event-stream 流式返回）")
def summary(
    kb_id: int, doc_id: int, body: SummaryIn | None = None,
    user: User = Depends(get_current_user), db: Connection = Depends(get_db),
):
    # 权限、配置和检索的错误在流式响应开始前抛出，按普通接口的 JSON 格式返回
    body = body or SummaryIn()
    doc, _ = doc_service.get_readable(db, user, kb_id, doc_id)
    focus = summary_service.normalize_focus(body.focus)
    fp = summary_service.fingerprint(db, doc)
    cached = None if body.refresh else summary_service.get_cached(db, doc, focus, fp)
    if cached:  # 文档没有变化，直接返回已保存的摘要，不需要大模型
        events = summary_service.stream_cached(cached)
    else:
        try:
            client = get_chat_client()
        except ChatError as e:
            raise BizError(f"文档摘要暂不可用：{e}", ErrorCode.LLM_UNAVAILABLE, 503)
        sources, plan = summary_service.prepare(db, user, doc, focus or None)
        save = partial(summary_service.save, doc, focus, fp, plan, sources, model=client.model)  # 完整生成后以摘要全文调用
        events = summary_service.stream_summary(client, doc, sources, plan, focus or None, on_complete=save)
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
