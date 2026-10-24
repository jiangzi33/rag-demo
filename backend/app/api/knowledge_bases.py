from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.knowledge_base import KnowledgeBaseCreateIn, KnowledgeBaseOut, KnowledgeBaseUpdateIn, OwnerTransferIn
from app.schemas.user import Page, Resp
from app.services import knowledge_base_service as kb_service

router = APIRouter(prefix="/knowledge-bases", tags=["知识库"])


@router.post("", response_model=Resp[KnowledgeBaseOut], summary="创建知识库（创建者即管理员）")
def create(body: KnowledgeBaseCreateIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    kb = kb_service.create(db, user, body.name)
    return Resp(data=KnowledgeBaseOut.model_validate(kb))


@router.get("", response_model=Resp[Page[KnowledgeBaseOut]], summary="我管理的知识库列表")
def list_mine(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64, description="匹配知识库名称"),
    user: User = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    items, total = kb_service.list_mine(db, user, page, size, keyword)
    return Resp(data=Page(items=[KnowledgeBaseOut.model_validate(kb) for kb in items], total=total, page=page, size=size))


@router.get("/{kb_id}", response_model=Resp[KnowledgeBaseOut], summary="知识库详情")
def detail(kb_id: int, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    kb = kb_service.get_managed_or_404(db, user, kb_id)
    return Resp(data=KnowledgeBaseOut.model_validate(kb))


@router.put("/{kb_id}", response_model=Resp[KnowledgeBaseOut], summary="修改知识库名称")
def rename(
    kb_id: int, body: KnowledgeBaseUpdateIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)
):
    kb = kb_service.rename(db, user, kb_id, body.name)
    return Resp(data=KnowledgeBaseOut.model_validate(kb))


@router.patch("/{kb_id}/owner", response_model=Resp[KnowledgeBaseOut], summary="转让知识库管理员")
def transfer_owner(
    kb_id: int, body: OwnerTransferIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)
):
    kb = kb_service.transfer_owner(db, user, kb_id, body.owner_id)
    return Resp(data=KnowledgeBaseOut.model_validate(kb))


@router.delete("/{kb_id}", response_model=Resp[None], summary="删除知识库")
def delete(kb_id: int, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    kb_service.delete(db, user, kb_id)
    return Resp(message="删除成功")
