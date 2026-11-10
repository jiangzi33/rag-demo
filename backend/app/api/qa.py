from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import Connection

from app.api.deps import get_current_user
from app.core.exceptions import BizError, ErrorCode
from app.db.session import get_db
from app.models.user import User
from app.rag.llm import ChatError, get_chat_client
from app.schemas.qa import ChatIn
from app.services import qa_service

router = APIRouter(prefix="/qa", tags=["智能问答"])


@router.post("/chat", summary="智能问答：检索相关片段并由大模型生成回答（text/event-stream 流式返回）")
def chat(body: ChatIn, user: User = Depends(get_current_user), db: Connection = Depends(get_db)):
    # 配置和检索的错误在流式响应开始前抛出，按普通接口的 JSON 格式返回
    try:
        client = get_chat_client()
    except ChatError as e:
        raise BizError(f"智能问答暂不可用：{e}", ErrorCode.LLM_UNAVAILABLE, 503)
    history = [m.model_dump() for m in body.history]
    sources = qa_service.retrieve(db, user, body.question, body.kb_ids, history, body.top_k)
    return StreamingResponse(
        qa_service.stream_answer(client, body.question.strip(), history, sources),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},  # 关闭反向代理缓冲，逐字输出
    )
