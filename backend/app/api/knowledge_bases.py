from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import Connection

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.knowledge_base import KnowledgeBaseCreateIn, KnowledgeBaseOut, KnowledgeBaseUpdateIn, OwnerTransferIn
from app.schemas.user import Page, Resp
from app.services import embedding_service
from app.services import knowledge_base_service as kb_service

router = APIRouter(prefix="/knowledge-bases", tags=["知识库"])


@router.post("", response_model=Resp[KnowledgeBaseOut], summary="创建知识库（创建者即管理员）")
def create(body: KnowledgeBaseCreateIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    kb = kb_service.create(db, user, body.name)
    return Resp(data=kb_service.to_out(db, user, [kb])[0])


@router.get("", response_model=Resp[Page[KnowledgeBaseOut]], summary="知识库列表（所有人可见，可只看我有权限的）")
def list_kbs(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    keyword: str | None = Query(None, max_length=64, description="匹配知识库名称"),
    scope: Literal["all", "mine"] = Query("all", description="all-全部知识库 mine-我拥有或被授权的"),
    user: User = Depends(get_current_user),
    db: Connection = Depends(get_db),
):
    items, total = kb_service.list_visible(db, user, page, size, keyword, only_mine=scope == "mine")
    return Resp(data=Page(items=kb_service.to_out(db, user, items), total=total, page=page, size=size))


@router.get("/{kb_id}", response_model=Resp[KnowledgeBaseOut], summary="知识库详情")
def detail(kb_id: int, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    kb = kb_service.get_visible(db, user, kb_id)
    return Resp(data=kb_service.to_out(db, user, [kb])[0])


@router.put("/{kb_id}", response_model=Resp[KnowledgeBaseOut], summary="修改知识库名称（需要管理权限）")
def rename(
    kb_id: int, body: KnowledgeBaseUpdateIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)
):
    kb = kb_service.rename(db, user, kb_id, body.name)
    return Resp(data=kb_service.to_out(db, user, [kb])[0])


@router.patch("/{kb_id}/owner", response_model=Resp[KnowledgeBaseOut], summary="转让知识库管理员（仅拥有者）")
def transfer_owner(
    kb_id: int, body: OwnerTransferIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)
):
    kb = kb_service.transfer_owner(db, user, kb_id, body.owner_id)
    return Resp(data=kb_service.to_out(db, user, [kb])[0])


@router.delete("/{kb_id}", response_model=Resp[None], summary="删除知识库（仅拥有者）")
def delete(
    kb_id: int, background_tasks: BackgroundTasks, user: User = Depends(get_current_user), db: Connection = Depends(get_db)
):
    kb_service.delete(db, user, kb_id)
    background_tasks.add_task(embedding_service.remove_knowledge_base, kb_id)
    return Resp(message="删除成功")

