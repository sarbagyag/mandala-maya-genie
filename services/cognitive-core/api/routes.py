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
<title>Maya Genie — Knowledge Base</title>
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
  .logo { font-size: 1.5rem; margin-bottom: 8px; }
  h1 { font-size: 1.2rem; font-weight: 600; color: #111; margin-bottom: 4px; }
  .subtitle { font-size: 0.85rem; color: #888; margin-bottom: 28px; }
  label { display: block; font-size: 0.8rem; font-weight: 500; color: #444; margin-bottom: 6px; }
  input[type="password"], input[type="text"] {
    width: 100%; border: 1px solid #ddd; border-radius: 8px;
    padding: 10px 12px; font-size: 0.9rem; outline: none; transition: border-color 0.15s;
  }
  input:focus { border-color: #6366f1; }
  .field { margin-bottom: 16px; }
  .btn-primary {
    width: 100%; background: #6366f1; color: #fff; border: none;
    border-radius: 8px; padding: 11px; font-size: 0.92rem; font-weight: 500;
    cursor: pointer; transition: background 0.15s;
    display: flex; align-items: center; justify-content: center; gap: 8px;
  }
  .btn-primary:hover:not(:disabled) { background: #4f46e5; }
  .btn-primary:disabled { background: #a5b4fc; cursor: not-allowed; }
  .banner {
    margin-top: 14px; padding: 11px 13px; border-radius: 8px;
    font-size: 0.85rem; display: none; align-items: flex-start; gap: 8px;
  }
  .banner.show { display: flex; }
  .banner.success { background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }
  .banner.error   { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
  .banner.info    { background: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; }
  .spinner {
    width: 13px; height: 13px; border: 2px solid currentColor;
    border-top-color: transparent; border-radius: 50%;
    animation: spin 0.7s linear infinite; flex-shrink: 0;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Main UI (hidden until authenticated) ── */
  #mainUI { display: none; }
  .topbar {
    display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px;
  }
  .topbar h1 { font-size: 1.1rem; }
  .session-pill {
    display: flex; align-items: center; gap: 6px;
    background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 99px;
    padding: 3px 10px; font-size: 0.75rem; color: #166534;
  }
  .session-pill .dot { width: 7px; height: 7px; background: #22c55e; border-radius: 50%; }
  .sign-out {
    background: none; border: none; color: #888; font-size: 0.75rem;
    cursor: pointer; padding: 0; text-decoration: underline;
  }
  .sign-out:hover { color: #444; }
  .section-title { font-size: 0.9rem; font-weight: 600; color: #111; margin-bottom: 14px; }
  .drop-zone {
    border: 2px dashed #ddd; border-radius: 8px; padding: 28px 16px;
    text-align: center; cursor: pointer;
    transition: border-color 0.15s, background 0.15s; position: relative; margin-bottom: 14px;
  }
  .drop-zone.drag-over { border-color: #6366f1; background: #f0f0ff; }
  .drop-zone input[type="file"] {
    position: absolute; inset: 0; opacity: 0; cursor: pointer; border: none; padding: 0;
  }
  .drop-zone .icon { font-size: 1.75rem; margin-bottom: 6px; }
  .drop-zone .hint { font-size: 0.78rem; color: #aaa; margin-top: 4px; }
  .drop-zone .chosen { font-size: 0.85rem; color: #6366f1; font-weight: 500; margin-top: 6px; }
  .progress-bar {
    height: 3px; background: #e5e7eb; border-radius: 2px;
    margin-bottom: 14px; overflow: hidden; display: none;
  }
  .progress-bar .fill {
    height: 100%; background: #6366f1; width: 0%; transition: width 0.4s ease; border-radius: 2px;
  }
  .fill.indeterminate { width: 40%; animation: slide 1.2s ease-in-out infinite; }
  @keyframes slide { 0% { transform: translateX(-100%); } 100% { transform: translateX(300%); } }
  hr { border: none; border-top: 1px solid #f0f0f0; margin: 24px 0; }
  .docs-header {
    display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;
  }
  .refresh-btn {
    background: none; border: none; color: #6366f1; font-size: 0.78rem;
    cursor: pointer; padding: 2px 6px; border-radius: 4px;
    display: flex; align-items: center; gap: 4px;
  }
  .refresh-btn:hover { background: #f0f0ff; }
  .doc-list { list-style: none; max-height: 320px; overflow-y: auto; }
  .doc-item {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 12px; border: 1px solid #eee; border-radius: 8px;
    margin-bottom: 8px; font-size: 0.85rem; transition: border-color 0.15s;
  }
  .doc-item.processing { border-color: #bfdbfe; background: #eff6ff; }
  .doc-item.deleting   { opacity: 0.45; pointer-events: none; }
  .doc-name { font-weight: 500; color: #222; word-break: break-all; }
  .doc-meta { font-size: 0.73rem; color: #999; margin-top: 3px; display: flex; align-items: center; gap: 5px; }
  .badge {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 0.68rem; font-weight: 500; padding: 1px 7px; border-radius: 99px;
  }
  .badge.processing { background: #dbeafe; color: #1d4ed8; }
  .badge .spinner { width: 9px; height: 9px; border-width: 1.5px; }
  .delete-btn {
    flex-shrink: 0; margin-left: 10px; background: none; border: 1px solid #fca5a5;
    color: #dc2626; border-radius: 6px; padding: 4px 10px; font-size: 0.76rem;
    cursor: pointer; transition: background 0.15s;
    display: flex; align-items: center; gap: 4px;
  }
  .delete-btn:hover:not(:disabled) { background: #fef2f2; }
  .delete-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .doc-error { font-size: 0.7rem; color: #dc2626; margin-top: 3px; }
  .list-msg { font-size: 0.83rem; color: #bbb; text-align: center; padding: 20px 0; }
  .list-loading { display: flex; align-items: center; justify-content: center; gap: 8px; color: #999; font-size: 0.83rem; padding: 20px 0; }
  .list-loading .spinner { color: #6366f1; }
</style>
</head>
<body>
<div class="card">

  <!-- ── Token gate ── -->
  <div id="gateUI">
    <div class="logo">🧠</div>
    <h1>Maya Genie</h1>
    <p class="subtitle">Knowledge Base Manager — enter your admin token to continue.</p>
    <form id="gateForm">
      <div class="field">
        <label for="tokenInput">Admin Token</label>
        <input type="password" id="tokenInput" placeholder="Paste your admin token" autocomplete="off" required>
      </div>
      <button type="submit" class="btn-primary" id="gateBtn">
        <span id="gateBtnText">Unlock</span>
      </button>
      <div class="banner" id="gateBanner"></div>
    </form>
  </div>

  <!-- ── Main UI ── -->
  <div id="mainUI">
    <div class="topbar">
      <h1>Knowledge Base</h1>
      <div style="display:flex;align-items:center;gap:10px">
        <div class="session-pill"><div class="dot"></div> Authenticated</div>
        <button class="sign-out" onclick="signOut()">Sign out</button>
      </div>
    </div>

    <p class="section-title">Upload Document</p>
    <div class="drop-zone" id="dropZone">
      <div class="icon">📄</div>
      <div style="font-size:0.88rem">Drag &amp; drop or <strong>click to browse</strong></div>
      <div class="hint">PDF, TXT, or Markdown — max 50 MB</div>
      <div class="chosen" id="chosenFile"></div>
      <input type="file" id="file" accept=".pdf,.txt,.md,.markdown">
    </div>
    <div class="progress-bar" id="progress"><div class="fill" id="fill"></div></div>
    <button class="btn-primary" id="uploadBtn" onclick="uploadFile()">
      <span id="uploadBtnText">Upload &amp; Ingest</span>
    </button>
    <div class="banner" id="uploadBanner"></div>

    <hr>

    <div class="docs-header">
      <p class="section-title" style="margin:0">Ingested Documents</p>
      <button class="refresh-btn" onclick="loadDocuments()">
        <span id="refreshSpinner" style="display:none" class="spinner"></span> Refresh
      </button>
    </div>
    <ul class="doc-list" id="docList">
      <li class="list-loading"><span class="spinner"></span> Loading…</li>
    </ul>
  </div>

</div>
<script>
  let AUTH_TOKEN = '';
  let pollTimer  = null;

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── Gate ──────────────────────────────────────────────
  document.getElementById('gateForm').addEventListener('submit', async e => {
    e.preventDefault();
    const t      = document.getElementById('tokenInput').value.trim();
    const btn    = document.getElementById('gateBtn');
    const btnTxt = document.getElementById('gateBtnText');
    const banner = document.getElementById('gateBanner');

    banner.className = 'banner';
    btn.disabled = true;
    btnTxt.innerHTML = '<span class="spinner" style="color:#fff;border-color:#fff;border-top-color:transparent"></span> Verifying…';

    try {
      const res = await fetch('/admin/documents', { headers: { 'Authorization': 'Bearer ' + t } });
      if (res.ok) {
        AUTH_TOKEN = t;
        document.getElementById('gateUI').style.display = 'none';
        document.getElementById('mainUI').style.display = 'block';
        const docs = await res.json();
        renderDocuments(docs);
      } else {
        banner.className = 'banner show error';
        banner.textContent = 'Invalid token. Please try again.';
        btn.disabled = false;
        btnTxt.textContent = 'Unlock';
      }
    } catch (err) {
      banner.className = 'banner show error';
      banner.textContent = 'Network error — ' + err.message;
      btn.disabled = false;
      btnTxt.textContent = 'Unlock';
    }
  });

  function signOut() {
    AUTH_TOKEN = '';
    clearTimeout(pollTimer);
    document.getElementById('mainUI').style.display = 'none';
    document.getElementById('gateUI').style.display = 'block';
    document.getElementById('tokenInput').value = '';
    document.getElementById('gateBanner').className = 'banner';
    document.getElementById('gateBtnText').textContent = 'Unlock';
    document.getElementById('gateBtn').disabled = false;
  }

  // ── Upload ────────────────────────────────────────────
  const dropZone  = document.getElementById('dropZone');
  const fileInput = document.getElementById('file');
  const chosenEl  = document.getElementById('chosenFile');

  fileInput.addEventListener('change', () => { chosenEl.textContent = fileInput.files[0]?.name || ''; });
  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault(); dropZone.classList.remove('drag-over');
    if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; chosenEl.textContent = e.dataTransfer.files[0].name; }
  });

  function showUploadBanner(type, html) {
    const b = document.getElementById('uploadBanner');
    b.className = 'banner show ' + type;
    b.innerHTML = (type === 'info' ? '<span class="spinner"></span>' : '') + '<span>' + html + '</span>';
  }

  async function uploadFile() {
    if (!fileInput.files[0]) { showUploadBanner('error', 'Please select a file first.'); return; }
    const btn     = document.getElementById('uploadBtn');
    const btnText = document.getElementById('uploadBtnText');
    const fill    = document.getElementById('fill');
    const progress = document.getElementById('progress');

    document.getElementById('uploadBanner').className = 'banner';
    btn.disabled = true;
    btnText.innerHTML = '<span class="spinner" style="color:#fff;border-color:#fff;border-top-color:transparent"></span> Uploading…';
    progress.style.display = 'block';
    fill.classList.remove('indeterminate');
    fill.style.transition = 'width 0.4s ease';
    fill.style.width = '40%';

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);

    try {
      const res  = await fetch('/admin/ingest', { method: 'POST', headers: { 'Authorization': 'Bearer ' + AUTH_TOKEN }, body: fd });
      const data = await res.json();

      if (res.ok) {
        fill.style.width = '70%';
        btnText.innerHTML = '<span class="spinner" style="color:#fff;border-color:#fff;border-top-color:transparent"></span> Processing…';
        showUploadBanner('info', 'Embedding "' + escHtml(data.filename) + '"… this may take a moment.');
        setTimeout(() => fill.classList.add('indeterminate'), 400);

        const poll = async () => {
          try {
            const r = await fetch('/admin/documents', { headers: { 'Authorization': 'Bearer ' + AUTH_TOKEN } });
            if (r.ok) {
              const fresh = await r.json();
              const done  = fresh.find(d => d.filename === data.filename && d.chunk_count > 0);
              if (done) {
                renderDocuments(fresh);
                fill.classList.remove('indeterminate');
                fill.style.transition = 'none'; fill.style.width = '100%';
                showUploadBanner('success', '"' + escHtml(data.filename) + '" ingested — ' + done.chunk_count + ' chunks added.');
                btn.disabled = false; btnText.textContent = 'Upload & Ingest';
                fileInput.value = ''; chosenEl.textContent = '';
                setTimeout(() => { progress.style.display = 'none'; fill.style.width = '0%'; fill.style.transition = 'width 0.4s ease'; }, 600);
                return;
              }
            }
          } catch (_) {}
          setTimeout(poll, 2000);
        };
        setTimeout(poll, 2000);
      } else {
        fill.style.width = '0%'; progress.style.display = 'none';
        showUploadBanner('error', data.detail || 'Upload failed.');
        btn.disabled = false; btnText.textContent = 'Upload & Ingest';
      }
    } catch (err) {
      fill.style.width = '0%'; progress.style.display = 'none';
      showUploadBanner('error', 'Network error — ' + err.message);
      btn.disabled = false; btnText.textContent = 'Upload & Ingest';
    }
  }

  // ── Documents ─────────────────────────────────────────
  async function loadDocuments() {
    const list = document.getElementById('docList');
    const spin = document.getElementById('refreshSpinner');
    list.innerHTML = '<li class="list-loading"><span class="spinner"></span> Loading…</li>';
    clearTimeout(pollTimer);
    spin.style.display = 'inline-block';
    try {
      const res = await fetch('/admin/documents', { headers: { 'Authorization': 'Bearer ' + AUTH_TOKEN } });
      if (!res.ok) { list.innerHTML = '<li class="list-msg">Could not load documents.</li>'; return; }
      renderDocuments(await res.json());
    } catch (e) {
      list.innerHTML = '<li class="list-msg">Network error.</li>';
    } finally {
      spin.style.display = 'none';
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
      return `<li class="doc-item${isProcessing ? ' processing' : ''}" id="doc-${d.id}">
        <div style="min-width:0">
          <div class="doc-name">${escHtml(d.filename)}</div>
          <div class="doc-meta">${meta}</div>
          <div class="doc-error" id="err-${d.id}" style="display:none"></div>
        </div>
        <button class="delete-btn" id="del-${d.id}" onclick="deleteDocument('${d.id}','${escHtml(d.filename)}')">Delete</button>
      </li>`;
    }).join('');
    if (docs.some(d => d.chunk_count === 0)) pollTimer = setTimeout(loadDocuments, 2500);
  }

  async function deleteDocument(id, name) {
    if (!confirm('Delete "' + name + '" and all its chunks from the knowledge base?')) return;
    const item  = document.getElementById('doc-' + id);
    const btn   = document.getElementById('del-' + id);
    const errEl = document.getElementById('err-' + id);
    item.classList.add('deleting');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner" style="color:#dc2626"></span> Deleting…';
    try {
      const res = await fetch('/admin/documents/' + id, { method: 'DELETE', headers: { 'Authorization': 'Bearer ' + AUTH_TOKEN } });
      if (res.ok) {
        item.remove();
        if (!document.getElementById('docList').children.length)
          document.getElementById('docList').innerHTML = '<li class="list-msg">No documents ingested yet.</li>';
      } else {
        item.classList.remove('deleting'); btn.disabled = false; btn.textContent = 'Delete';
        errEl.textContent = 'Delete failed. Try again.'; errEl.style.display = 'block';
      }
    } catch (e) {
      item.classList.remove('deleting'); btn.disabled = false; btn.textContent = 'Delete';
      errEl.textContent = 'Network error. Try again.'; errEl.style.display = 'block';
    }
  }
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
