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
from db.persistence import upsert_conversation, save_messages, list_documents, delete_document

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
    background: #f5f5f5; min-height: 100vh;
    display: flex; align-items: center; justify-content: center; padding: 24px;
  }
  .card {
    background: #fff; border-radius: 12px;
    box-shadow: 0 2px 16px rgba(0,0,0,0.08);
    padding: 40px; width: 100%; max-width: 480px;
  }
  h1 { font-size: 1.25rem; font-weight: 600; color: #111; margin-bottom: 4px; }
  .subtitle { font-size: 0.875rem; color: #666; margin-bottom: 32px; }
  label { display: block; font-size: 0.8rem; font-weight: 500; color: #444; margin-bottom: 6px; }
  input[type="password"] {
    width: 100%; border: 1px solid #ddd; border-radius: 8px;
    padding: 10px 12px; font-size: 0.9rem; outline: none; transition: border-color 0.15s;
  }
  input[type="password"]:focus { border-color: #6366f1; }
  .field { margin-bottom: 20px; }
  .drop-zone {
    border: 2px dashed #ddd; border-radius: 8px; padding: 32px 16px;
    text-align: center; cursor: pointer;
    transition: border-color 0.15s, background 0.15s; position: relative;
  }
  .drop-zone.drag-over { border-color: #6366f1; background: #f0f0ff; }
  .drop-zone input[type="file"] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer; border: none; padding: 0;
  }
  .drop-zone .icon { font-size: 2rem; margin-bottom: 8px; }
  .drop-zone .hint { font-size: 0.8rem; color: #888; margin-top: 4px; }
  .drop-zone .chosen { font-size: 0.875rem; color: #6366f1; font-weight: 500; margin-top: 8px; }
  button[type="submit"] {
    width: 100%; background: #6366f1; color: #fff; border: none;
    border-radius: 8px; padding: 12px; font-size: 0.95rem; font-weight: 500;
    cursor: pointer; transition: background 0.15s; margin-top: 4px;
    display: flex; align-items: center; justify-content: center; gap: 8px;
  }
  button[type="submit"]:hover:not(:disabled) { background: #4f46e5; }
  button[type="submit"]:disabled { background: #a5b4fc; cursor: not-allowed; }
  .progress-bar {
    height: 4px; background: #e5e7eb; border-radius: 2px;
    margin-top: 16px; overflow: hidden; display: none;
  }
  .progress-bar .fill {
    height: 100%; background: #6366f1; width: 0%;
    transition: width 0.4s ease; border-radius: 2px;
  }
  .progress-bar .fill.indeterminate {
    width: 40%; animation: slide 1.2s ease-in-out infinite;
  }
  @keyframes slide {
    0%   { transform: translateX(-100%); }
    100% { transform: translateX(300%); }
  }
  .banner {
    margin-top: 16px; padding: 12px 14px; border-radius: 8px;
    font-size: 0.875rem; display: none; align-items: flex-start; gap: 8px;
  }
  .banner.show { display: flex; }
  .banner.success { background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }
  .banner.error   { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
  .banner.info    { background: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; }
  .spinner {
    width: 14px; height: 14px; border: 2px solid currentColor;
    border-top-color: transparent; border-radius: 50%;
    animation: spin 0.7s linear infinite; flex-shrink: 0; margin-top: 1px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  hr { border: none; border-top: 1px solid #f0f0f0; margin: 28px 0; }
  .docs-header {
    display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;
  }
  .docs-header h2 { font-size: 0.95rem; font-weight: 600; color: #111; }
  .refresh-btn {
    background: none; border: none; color: #6366f1; font-size: 0.8rem;
    cursor: pointer; padding: 2px 6px; border-radius: 4px;
  }
  .refresh-btn:hover { background: #f0f0ff; }
  .doc-list { list-style: none; }
  .doc-item {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 12px; border: 1px solid #eee; border-radius: 8px;
    margin-bottom: 8px; font-size: 0.85rem; transition: border-color 0.15s;
  }
  .doc-item.processing { border-color: #bfdbfe; background: #eff6ff; }
  .doc-item.deleting   { opacity: 0.5; pointer-events: none; }
  .doc-name { font-weight: 500; color: #222; word-break: break-all; }
  .doc-meta { font-size: 0.75rem; color: #888; margin-top: 3px; display: flex; align-items: center; gap: 6px; }
  .badge {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 0.7rem; font-weight: 500; padding: 1px 6px; border-radius: 99px;
  }
  .badge.processing { background: #dbeafe; color: #1d4ed8; }
  .badge .spinner { width: 10px; height: 10px; border-width: 1.5px; }
  .delete-btn {
    flex-shrink: 0; margin-left: 12px; background: none; border: 1px solid #fca5a5;
    color: #dc2626; border-radius: 6px; padding: 4px 10px; font-size: 0.78rem;
    cursor: pointer; transition: background 0.15s, opacity 0.15s;
    display: flex; align-items: center; gap: 5px;
  }
  .delete-btn:hover { background: #fef2f2; }
  .delete-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .doc-error { font-size: 0.72rem; color: #dc2626; margin-top: 3px; }
  .list-msg { font-size: 0.85rem; color: #aaa; text-align: center; padding: 16px 0; }
  .list-loading { display: flex; align-items: center; justify-content: center; gap: 8px; color: #888; font-size: 0.85rem; padding: 16px 0; }
  .list-loading .spinner { color: #6366f1; }
</style>
</head>
<body>
<div class="card">
  <h1>Knowledge Base Ingestion</h1>
  <p class="subtitle">Upload a document to add it to Maya Genie's knowledge base.</p>

  <form id="form">
    <div class="field">
      <label for="token">Admin Token</label>
      <input type="password" id="token" placeholder="Enter admin token" autocomplete="off" required>
    </div>

    <div class="field">
      <label>Document</label>
      <div class="drop-zone" id="dropZone">
        <div class="icon">📄</div>
        <div>Drag &amp; drop or <strong>click to browse</strong></div>
        <div class="hint">PDF, TXT, or Markdown — max 50 MB</div>
        <div class="chosen" id="chosenFile"></div>
        <input type="file" id="file" accept=".pdf,.txt,.md,.markdown" required>
      </div>
    </div>

    <button type="submit" id="btn">
      <span id="btnText">Upload &amp; Ingest</span>
    </button>
    <div class="progress-bar" id="progress"><div class="fill" id="fill"></div></div>
    <div class="banner" id="uploadBanner"></div>
  </form>

  <hr>

  <div>
    <div class="docs-header">
      <h2>Ingested Documents</h2>
      <button class="refresh-btn" onclick="loadDocuments()">Refresh</button>
    </div>
    <ul class="doc-list" id="docList">
      <li class="list-loading"><span class="spinner"></span> Loading…</li>
    </ul>
  </div>
</div>

<script>
  const dropZone  = document.getElementById('dropZone');
  const fileInput = document.getElementById('file');
  const chosenEl  = document.getElementById('chosenFile');
  let pollTimer   = null;

  fileInput.addEventListener('change', () => {
    chosenEl.textContent = fileInput.files[0]?.name || '';
  });
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault(); dropZone.classList.remove('drag-over');
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      chosenEl.textContent = e.dataTransfer.files[0].name;
    }
  });

  function token() { return document.getElementById('token').value.trim(); }

  function showBanner(type, html) {
    const b = document.getElementById('uploadBanner');
    b.className = 'banner show ' + type;
    b.innerHTML = (type === 'info' ? '<span class="spinner"></span>' : '') + '<span>' + html + '</span>';
  }
  function hideBanner() {
    document.getElementById('uploadBanner').className = 'banner';
  }

  async function loadDocuments() {
    if (!token()) return;
    const list = document.getElementById('docList');
    list.innerHTML = '<li class="list-loading"><span class="spinner"></span> Loading…</li>';
    clearTimeout(pollTimer);
    try {
      const res = await fetch('/admin/documents', { headers: { 'Authorization': 'Bearer ' + token() } });
      if (res.status === 401) { list.innerHTML = '<li class="list-msg">Invalid token.</li>'; return; }
      if (!res.ok)            { list.innerHTML = '<li class="list-msg">Could not load documents.</li>'; return; }
      const docs = await res.json();
      renderDocuments(docs);
    } catch (e) {
      list.innerHTML = '<li class="list-msg">Network error loading documents.</li>';
    }
  }

  function renderDocuments(docs) {
    const list = document.getElementById('docList');
    if (!docs.length) { list.innerHTML = '<li class="list-msg">No documents ingested yet.</li>'; return; }

    list.innerHTML = docs.map(d => {
      const isProcessing = d.chunk_count === 0;
      const meta = isProcessing
        ? '<span class="badge processing"><span class="spinner"></span> Processing…</span>'
        : d.chunk_count + ' chunks &middot; ' + new Date(d.ingested_at).toLocaleString();
      return `<li class="doc-item ${isProcessing ? 'processing' : ''}" id="doc-${d.id}">
        <div style="min-width:0">
          <div class="doc-name">${escHtml(d.filename)}</div>
          <div class="doc-meta">${meta}</div>
          <div class="doc-error" id="err-${d.id}" style="display:none"></div>
        </div>
        <button class="delete-btn" id="del-${d.id}" onclick="deleteDocument('${d.id}', '${escHtml(d.filename)}')">
          Delete
        </button>
      </li>`;
    }).join('');

    if (docs.some(d => d.chunk_count === 0)) {
      pollTimer = setTimeout(loadDocuments, 2500);
    }
  }

  async function deleteDocument(id, name) {
    if (!token()) return;
    if (!confirm('Delete "' + name + '" and all its chunks from the knowledge base?')) return;

    const item = document.getElementById('doc-' + id);
    const btn  = document.getElementById('del-' + id);
    const errEl = document.getElementById('err-' + id);

    item.classList.add('deleting');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner" style="color:#dc2626"></span> Deleting…';

    try {
      const res = await fetch('/admin/documents/' + id, {
        method: 'DELETE',
        headers: { 'Authorization': 'Bearer ' + token() }
      });
      if (res.ok) {
        item.remove();
        const list = document.getElementById('docList');
        if (!list.children.length) list.innerHTML = '<li class="list-msg">No documents ingested yet.</li>';
      } else {
        item.classList.remove('deleting');
        btn.disabled = false;
        btn.innerHTML = 'Delete';
        errEl.textContent = 'Delete failed. Try again.';
        errEl.style.display = 'block';
      }
    } catch (e) {
      item.classList.remove('deleting');
      btn.disabled = false;
      btn.innerHTML = 'Delete';
      errEl.textContent = 'Network error. Try again.';
      errEl.style.display = 'block';
    }
  }

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  document.getElementById('token').addEventListener('blur', loadDocuments);

  document.getElementById('form').addEventListener('submit', async e => {
    e.preventDefault();
    const file    = fileInput.files[0];
    const btn     = document.getElementById('btn');
    const btnText = document.getElementById('btnText');
    const fill    = document.getElementById('fill');
    const progress = document.getElementById('progress');

    hideBanner();
    btn.disabled = true;
    btnText.innerHTML = '<span class="spinner" style="color:#fff;border-color:#fff;border-top-color:transparent"></span> Uploading…';
    progress.style.display = 'block';
    fill.classList.remove('indeterminate');
    fill.style.width = '40%';

    const fd = new FormData();
    fd.append('file', file);

    try {
      const res  = await fetch('/admin/ingest', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token() },
        body: fd,
      });
      const data = await res.json();

      if (res.ok) {
        fill.style.width = '100%';
        btnText.innerHTML = '<span class="spinner" style="color:#fff;border-color:#fff;border-top-color:transparent"></span> Processing…';
        showBanner('info', 'Embedding "' + escHtml(data.filename) + '"… this may take a moment.');
        setTimeout(() => {
          fill.classList.add('indeterminate');
        }, 400);

        // Poll until the new document appears with chunk_count > 0
        const poll = async () => {
          try {
            const r = await fetch('/admin/documents', { headers: { 'Authorization': 'Bearer ' + token() } });
            if (r.ok) {
              const docs = await res.json().catch(() => []);
              const fresh = await r.json();
              const done = fresh.find(d => d.filename === data.filename && d.chunk_count > 0);
              if (done) {
                renderDocuments(fresh);
                fill.classList.remove('indeterminate');
                fill.style.transition = 'none'; fill.style.width = '100%';
                showBanner('success', 'Done! "' + escHtml(data.filename) + '" ingested — ' + done.chunk_count + ' chunks added to the knowledge base.');
                btn.disabled = false;
                btnText.textContent = 'Upload & Ingest';
                setTimeout(() => { progress.style.display = 'none'; fill.style.width = '0%'; fill.style.transition = ''; }, 600);
                return;
              }
            }
          } catch (_) {}
          setTimeout(poll, 2000);
        };
        setTimeout(poll, 2000);

      } else {
        fill.style.width = '0%'; progress.style.display = 'none';
        showBanner('error', data.detail || 'Upload failed.');
        btn.disabled = false;
        btnText.textContent = 'Upload & Ingest';
      }
    } catch (err) {
      fill.style.width = '0%'; progress.style.display = 'none';
      showBanner('error', 'Network error — ' + err.message);
      btn.disabled = false;
      btnText.textContent = 'Upload & Ingest';
    }
  });

  loadDocuments();
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

    original_name = file.filename

    def _ingest(path: str):
        try:
            count = ingest_file(path, original_filename=original_name)
            logger.info(f"Background ingestion complete: {count} chunks from {original_name}")
        except Exception as e:
            logger.error(f"Background ingestion failed: {e}", exc_info=True)
        finally:
            os.unlink(path)

    background_tasks.add_task(_ingest, tmp.name)

    return {
        "status": "ingestion_started",
        "filename": file.filename,
    }


@router.get("/admin/documents")
async def admin_list_documents(authorization: str = Header(...)):
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if authorization != f"Bearer {admin_token}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return list_documents()


@router.delete("/admin/documents/{doc_id}")
async def admin_delete_document(doc_id: str, authorization: str = Header(...)):
    admin_token = os.getenv("ADMIN_TOKEN", "")
    if authorization != f"Bearer {admin_token}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        delete_document(doc_id)
    except Exception as e:
        logger.error(f"Failed to delete document {doc_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete document")
    return {"status": "deleted", "id": doc_id}
