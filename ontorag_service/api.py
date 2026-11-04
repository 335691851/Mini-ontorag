import hashlib
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from .config import API_KEY, MAX_UPLOAD_BYTES
from .db import Document, Session, document_data, get_document, init_db, list_documents
from .graph import describe, select_query
from .storage import get_bytes, put_bytes
from .mcp_api import mcp

ALLOWED = {".md", ".txt", ".html", ".htm", ".epub", ".pdf"}
mcp_app = mcp.http_app(path="/", stateless_http=True, json_response=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    async with mcp_app.lifespan(_app):
        yield


app = FastAPI(title="OntoRAG Integration API", version="1.0.0", lifespan=lifespan)
app.mount("/mcp", mcp_app)


@app.middleware("http")
async def authenticate(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    expected = API_KEY
    supplied = request.headers.get("authorization", "")
    if not expected or not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:], expected):
        return Response(content='{"detail":"unauthorized"}', status_code=401, media_type="application/json")
    return await call_next(request)


@app.get("/health")
def health():
    return {"ok": True, "service": "ontorag-api"}


@app.post("/v1/documents", status_code=202)
async def upload_document(file: UploadFile = File(...), idempotency_key: str | None = Header(default=None)):
    filename = (file.filename or "").split("/")[-1].split("\\")[-1]
    if not filename or len(filename) > 255 or not any(filename.lower().endswith(ext) for ext in ALLOWED):
        raise HTTPException(415, "supported formats: .md, .txt, .html, .htm, .epub, .pdf")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file must be between 1 and {MAX_UPLOAD_BYTES} bytes")
    digest = hashlib.sha256(content).hexdigest()
    # The idempotency key is scoped to one input hash; do not expose raw client keys in the database.
    document_id = str(uuid4()) if not idempotency_key else str(uuid5(NAMESPACE_URL, idempotency_key))
    with Session() as session:
        existing = session.get(Document, document_id)
        if existing:
            if existing.sha256 != digest:
                raise HTTPException(409, "idempotency key was used for different content")
            return document_data(existing)
    source_key = put_bytes(f"sources/{document_id}/{filename}", content, file.content_type or "application/octet-stream")
    doc = Document(id=document_id, filename=filename, content_type=file.content_type or "application/octet-stream", sha256=digest, source_key=source_key)
    try:
        with Session.begin() as session:
            session.add(doc)
    except Exception:
        with Session() as session:
            existing = session.get(Document, document_id)
            if existing and existing.sha256 == digest:
                return document_data(existing)
        raise
    return document_data(doc)


@app.get("/v1/documents")
def documents(limit: int = Query(50, ge=1, le=100)):
    return {"items": list_documents(limit)}


@app.get("/v1/documents/{document_id}")
def document(document_id: str):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "document not found")
    return document_data(doc)


@app.get("/v1/documents/{document_id}/graph")
def graph(document_id: str):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "document not found")
    if doc.status != "completed" or not doc.graph_key:
        raise HTTPException(409, "graph is not ready")
    return Response(get_bytes(doc.graph_key), media_type="text/turtle")


@app.post("/v1/documents/{document_id}/retry", status_code=202)
def retry_document(document_id: str):
    with Session.begin() as session:
        doc = session.get(Document, document_id)
        if not doc:
            raise HTTPException(404, "document not found")
        if doc.status != "failed":
            raise HTTPException(409, "only failed documents can be retried")
        doc.status = "queued"
        doc.stage = None
        doc.error = None
        doc.attempts = 0
        doc.lease_until = None
        doc.updated_at = datetime.now(timezone.utc)
        return document_data(doc)


@app.post("/v1/documents/{document_id}/sparql")
async def sparql(document_id: str, request: Request):
    query = (await request.body()).decode("utf-8")
    try:
        return select_query(document_id, query)
    except FileNotFoundError as exc:
        raise HTTPException(404 if str(exc) == "document not found" else 409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/documents/{document_id}/describe")
def describe_iri(document_id: str, iri: str):
    try:
        return {"iri": iri, "properties": describe(document_id, iri)}
    except FileNotFoundError as exc:
        raise HTTPException(404 if str(exc) == "document not found" else 409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
