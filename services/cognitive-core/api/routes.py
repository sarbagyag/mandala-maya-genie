import logging
import os
from fastapi import APIRouter, HTTPException, Header, UploadFile, File, BackgroundTasks
import tempfile
import shutil

from schemas.message import ChatRequest, ChatResponse
from rag.pipeline import run_pipeline
from rag.ingestion import ingest_file
from llm.client import get_llm

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    history = [msg.model_dump() for msg in request.conversation_history]

    try:
        result = await run_pipeline(
            message=request.message,
            conversation_history=history,
        )
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate response")

    llm = get_llm()
    model_name = getattr(llm, "model", None) or getattr(llm, "model_name", "unknown")

    return ChatResponse(
        session_id=request.session_id,
        response=result["response"],
        sources=result["sources"],
        model_used=str(model_name),
    )


@router.post("/admin/ingest")
async def admin_ingest(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    authorization: str = Header(...),
):
    admin_token = os.getenv("ADMIN_TOKEN", "")
    expected = f"Bearer {admin_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".pdf", ".txt"):
        raise HTTPException(status_code=400, detail="Only .pdf and .txt files supported")

    # Save uploaded file to temp location
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        shutil.copyfileobj(file.file, tmp)
        tmp.close()
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise

    def _ingest(path: str):
        try:
            count = ingest_file(path)
            logger.info(f"Background ingestion complete: {count} chunks from {file.filename}")
        except Exception as e:
            logger.error(f"Background ingestion failed: {e}", exc_info=True)
        finally:
            os.unlink(path)

    background_tasks.add_task(_ingest, tmp.name)

    return {
        "status": "ingestion_started",
        "filename": file.filename,
    }
