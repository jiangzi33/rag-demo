from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy import Connection

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.search import SearchIn, SearchResultOut
from app.schemas.user import Resp
from app.services.retrieval_service import get_retriever

router = APIRouter(prefix="/search", tags=["内容检索"])


@router.post("", response_model=Resp[list[SearchResultOut]], summary="内容检索：按语义相似度检索文档片段（只含有读权限的文档）")
def search(body: SearchIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    chunks = get_retriever().retrieve(db, user, body.query, body.kb_ids, body.top_k)
    return Resp(data=[SearchResultOut(**asdict(c)) for c in chunks])
