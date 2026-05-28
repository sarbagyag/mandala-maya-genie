import logging
import os
from fastapi import APIRouter, HTTPException, Header, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse
import tempfile
import shutil

from schemas.message import ChatRequest, ChatResponse
from rag.pipeline import run_pipeline
from rag.ingestion import ingest_file
from llm.client import get_llm
from db.persistence import upsert_conversation, save_messages

logger = logging.getLogger(__name__)

router = APIRouter()


_UPLOAD_UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Maya Genie — Knowledge Base Ingestion</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f5f5f5;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }
  .card {
    background: #fff;
    border-radius: 12px;
    box-shadow: 0 2px 16px rgba(0,0,0,0.08);
    padding: 40px;
    width: 100%;
    max-width: 480px;
  }
  h1 { font-size: 1.25rem; font-weight: 600; color: #111; margin-bottom: 4px; }
  .subtitle { font-size: 0.875rem; color: #666; margin-bottom: 32px; }
  label { display: block; font-size: 0.8rem; font-weight: 500; color: #444; margin-bottom: 6px; }
  input[type="password"], input[type="file"] {
    width: 100%;
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 0.9rem;
    outline: none;
    transition: border-color 0.15s;
  }
  input[type="password"]:focus, input[type="file"]:focus { border-color: #6366f1; }
  .field { margin-bottom: 20px; }
  .drop-zone {
    border: 2px dashed #ddd;
    border-radius: 8px;
    padding: 32px 16px;
    text-align: center;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
    position: relative;
  }
  .drop-zone.drag-over { border-color: #6366f1; background: #f0f0ff; }
  .drop-zone input[type="file"] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer;
    border: none; padding: 0;
  }
  .drop-zone .icon { font-size: 2rem; margin-bottom: 8px; }
  .drop-zone .hint { font-size: 0.8rem; color: #888; margin-top: 4px; }
  .drop-zone .filename { font-size: 0.875rem; color: #6366f1; font-weight: 500; margin-top: 8px; }
  button[type="submit"] {
    width: 100%;
    background: #6366f1;
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 12px;
    font-size: 0.95rem;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.15s;
    margin-top: 4px;
  }
  button[type="submit"]:hover { background: #4f46e5; }
  button[type="submit"]:disabled { background: #a5b4fc; cursor: not-allowed; }
  .progress-bar {
    height: 4px; background: #e5e7eb; border-radius: 2px;
    margin-top: 16px; overflow: hidden; display: none;
  }
  .progress-bar .fill {
    height: 100%; background: #6366f1; width: 0%;
    transition: width 0.3s; border-radius: 2px;
  }
  .message {
    margin-top: 16px; padding: 12px 14px; border-radius: 8px;
    font-size: 0.875rem; display: none;
  }
  .message.success { background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }
  .message.error   { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
</style>
</head>
<body>
<div class="card">
  <h1>Knowledge Base Ingestion</h1>
  <p class="subtitle">Upload a document to add it to Maya Genie's knowledge base.</p>

  <form id="form">
    <div class="field">
      <label for="token">Admin Token</label>
      <input type="password" id="token" placeholder="Bearer token" autocomplete="off" required>
    </div>

    <div class="field">
      <label>Document</label>
      <div class="drop-zone" id="dropZone">
        <div class="icon">📄</div>
        <div>Drag &amp; drop or <strong>click to browse</strong></div>
        <div class="hint">PDF, TXT, or Markdown — max 50 MB</div>
        <div class="filename" id="filename"></div>
        <input type="file" id="file" accept=".pdf,.txt,.md,.markdown" required>
      </div>
    </div>

    <button type="submit" id="btn">Upload &amp; Ingest</button>
    <div class="progress-bar" id="progress"><div class="fill" id="fill"></div></div>
    <div class="message" id="msg"></div>
  </form>
</div>

<script>
  const dropZone = document.getElementById('dropZone');
  const fileInput = document.getElementById('file');
  const filenameEl = document.getElementById('filename');

  fileInput.addEventListener('change', () => {
    filenameEl.textContent = fileInput.files[0]?.name || '';
  });
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const dt = e.dataTransfer;
    if (dt.files.length) {
      fileInput.files = dt.files;
      filenameEl.textContent = dt.files[0].name;
    }
  });

  document.getElementById('form').addEventListener('submit', async e => {
    e.preventDefault();
    const token = document.getElementById('token').value.trim();
    const file = fileInput.files[0];
    const btn = document.getElementById('btn');
    const progress = document.getElementById('progress');
    const fill = document.getElementById('fill');
    const msg = document.getElementById('msg');

    msg.style.display = 'none';
    btn.disabled = true;
    btn.textContent = 'Uploading…';
    progress.style.display = 'block';
    fill.style.width = '30%';

    const fd = new FormData();
    fd.append('file', file);

    try {
      fill.style.width = '60%';
      const res = await fetch('/admin/ingest', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token },
        body: fd,
      });
      fill.style.width = '100%';
      const data = await res.json();
      if (res.ok) {
        msg.className = 'message success';
        msg.textContent = '✓ Ingestion started for "' + data.filename + '". The document will be available shortly.';
      } else {
        msg.className = 'message error';
        msg.textContent = '✗ ' + (data.detail || 'Upload failed.');
      }
    } catch (err) {
      msg.className = 'message error';
      msg.textContent = '✗ Network error — ' + err.message;
    } finally {
      msg.style.display = 'block';
      btn.disabled = false;
      btn.textContent = 'Upload & Ingest';
      setTimeout(() => { fill.style.width = '0%'; progress.style.display = 'none'; }, 800);
    }
  });
</script>
</body>
</html>"""


@router.get("/admin", response_class=HTMLResponse)
async def admin_ui():
    return _UPLOAD_UI


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

    try:
        conv_id = upsert_conversation(request.session_id, request.channel)
        save_messages(conv_id, request.message, result["response"])
    except Exception as e:
        logger.warning(f"Failed to persist conversation to DB: {e}")

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
    if ext not in (".pdf", ".txt", ".md", ".markdown"):
        raise HTTPException(status_code=400, detail="Only .pdf, .txt, and .md files supported")

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
