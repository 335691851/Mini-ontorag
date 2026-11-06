import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.openapi.utils import get_openapi
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .config import API_KEYS, MAX_UPLOAD_BYTES
from .db import Document, Session, archive_graph, document_data, get_document, init_db, list_documents
from .graph import describe, graph_for, select_query, select_domain_query, domain_summary
from .storage import delete_bytes, get_bytes, put_bytes
from .mcp_api import mcp
from .ontology import valid_schema_turtle
from .domain import ONTOLOGY_VERSION, schema_card, schema_turtle, validate_instance_graph, normalize_document_graph
from .quality import graph_quality_report
from rdflib import Graph

ALLOWED = {".md", ".txt", ".html", ".htm", ".epub", ".pdf"}
logger = logging.getLogger(__name__)


class DocumentOut(BaseModel):
    id: str
    filename: str
    sha256: str
    status: str
    stage: str | None
    error: str | None
    quality: dict | None
    reviewed_at: datetime | None
    review_note: str | None
    attempts: int
    ontology_version: str | None
    created_at: datetime
    updated_at: datetime


class DocumentsOut(BaseModel):
    items: list[DocumentOut]


class PublishRequest(BaseModel):
    note: str = Field(min_length=3, max_length=1000)


mcp_app = mcp.http_app(path="/", stateless_http=True, json_response=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    async with mcp_app.lifespan(_app):
        yield


app = FastAPI(title="OntoRAG Integration API", version="1.0.0", lifespan=lifespan)
app.mount("/mcp", mcp_app)


def service_openapi():
    if app.openapi_schema is None:
        spec = get_openapi(title=app.title, version=app.version, routes=app.routes)
        spec.setdefault("components", {}).setdefault("securitySchemes", {})["ServiceBearer"] = {"type": "http", "scheme": "bearer"}
        for path, methods in spec["paths"].items():
            if path != "/health":
                for operation in methods.values():
                    operation["security"] = [{"ServiceBearer": []}]
        app.openapi_schema = spec
    return app.openapi_schema


app.openapi = service_openapi


@app.middleware("http")
async def authenticate(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    supplied = request.headers.get("authorization", "")
    valid = supplied.startswith("Bearer ") and any(hmac.compare_digest(supplied[7:], key) for key in API_KEYS)
    if not valid:
        return Response(content='{"detail":"unauthorized"}', status_code=401, media_type="application/json", headers={"Cache-Control": "no-store"})
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/health")
def health():
    return {"ok": True, "service": "ontorag-api"}


@app.post("/v1/documents", status_code=202, response_model=DocumentOut, tags=["Documents"])
async def upload_document(response: Response, file: UploadFile = File(...), idempotency_key: str | None = Header(default=None)):
    if idempotency_key is not None and (not 1 <= len(idempotency_key) <= 128 or any(ord(c) < 33 or ord(c) > 126 for c in idempotency_key)):
        raise HTTPException(400, "Idempotency-Key must be 1-128 printable ASCII characters without spaces")
    filename = (file.filename or "").split("/")[-1].split("\\")[-1]
    if not filename or len(filename) > 255 or not any(filename.lower().endswith(ext) for ext in ALLOWED):
        raise HTTPException(415, "supported formats: .md, .txt, .html, .htm, .epub, .pdf")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file must be between 1 and {MAX_UPLOAD_BYTES} bytes")
    digest = hashlib.sha256(content).hexdigest()
    # The idempotency key is scoped to one input hash; do not expose raw client keys in the database.
    document_id = str(uuid4()) if not idempotency_key else str(uuid5(NAMESPACE_URL, idempotency_key))
    response.headers["Location"] = f"/v1/documents/{document_id}"
    response.headers["Retry-After"] = "5"
    with Session() as session:
        existing = session.get(Document, document_id)
        if existing:
            if existing.sha256 != digest:
                raise HTTPException(409, "idempotency key was used for different content")
            return document_data(existing)
    # A unique blob pathname keeps concurrent retries from colliding before the DB row is committed.
    extension = "." + filename.rsplit(".", 1)[-1].lower()
    source_key = put_bytes(f"sources/{document_id}/{uuid4().hex}/source{extension}", content, file.content_type or "application/octet-stream")
    doc = Document(id=document_id, filename=filename, content_type=file.content_type or "application/octet-stream", sha256=digest, source_key=source_key)
    try:
        with Session.begin() as session:
            session.add(doc)
    except Exception:
        try:
            delete_bytes([source_key])
        except Exception:
            logger.exception("Could not clean up an unreferenced uploaded blob")
        with Session() as session:
            existing = session.get(Document, document_id)
            if existing and existing.sha256 == digest:
                return document_data(existing)
        raise
    return document_data(doc)


@app.get("/v1/documents", response_model=DocumentsOut, tags=["Documents"])
def documents(limit: int = Query(50, ge=1, le=100)):
    return {"items": list_documents(limit)}


@app.get("/v1/documents/{document_id}", response_model=DocumentOut, tags=["Documents"])
def document(document_id: str):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "document not found")
    return document_data(doc)


def _active_worker_document(document_id: str, token: str | None) -> Document:
    doc = get_document(document_id)
    if not doc or not token or not doc.lease_token or not hmac.compare_digest(doc.lease_token, token) or doc.status != "processing":
        raise HTTPException(409, "worker lease is not active")
    return doc


@app.get("/v1/internal/documents/{document_id}/source", include_in_schema=False)
def worker_source(document_id: str, x_worker_lease: str | None = Header(default=None)):
    doc = _active_worker_document(document_id, x_worker_lease)
    return Response(get_bytes(doc.source_key), media_type=doc.content_type)


@app.post("/v1/internal/documents/{document_id}/artifacts", include_in_schema=False)
async def worker_artifacts(
    document_id: str,
    graph: UploadFile = File(...),
    schema_file: UploadFile = File(..., alias="schema"),
    x_worker_lease: str | None = Header(default=None),
    x_ontology_version: str | None = Header(default=None),
    diagnostics: str = Form(default="{}"),
):
    worker_doc = _active_worker_document(document_id, x_worker_lease)
    if x_ontology_version != ONTOLOGY_VERSION:
        raise HTTPException(409, "unknown ontology version")
    graph_data = await graph.read(MAX_UPLOAD_BYTES + 1)
    schema_data = valid_schema_turtle(await schema_file.read(MAX_UPLOAD_BYTES + 1), document_id)
    if not graph_data or not schema_data or len(graph_data) + len(schema_data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "graph and schema must be nonempty and at most MAX_UPLOAD_BYTES combined")
    try:
        if len(diagnostics) > 20000:
            raise ValueError("extraction diagnostics too large")
        extraction = json.loads(diagnostics)
        if not isinstance(extraction, dict):
            raise ValueError("extraction diagnostics must be an object")
        parsed_graph = Graph().parse(data=graph_data.decode("utf-8"), format="turtle")
        if normalize_document_graph(parsed_graph, worker_doc.filename):
            graph_data = parsed_graph.serialize(format="turtle").encode("utf-8")
        validate_instance_graph(parsed_graph)
        quality = graph_quality_report(parsed_graph, extraction)
        if set(Graph().parse(data=schema_data.decode("utf-8"), format="turtle")) != set(Graph().parse(data=schema_turtle().decode("utf-8"), format="turtle")):
            raise ValueError("schema does not match current domain ontology")
    except Exception as exc:
        raise HTTPException(422, f"invalid domain graph: {exc}") from exc
    graph_key = schema_key = None
    try:
        graph_key = put_bytes(f"graphs/{document_id}/{x_worker_lease}/world.ttl", graph_data, "text/turtle")
        schema_key = put_bytes(f"graphs/{document_id}/{x_worker_lease}/schema.ttl", schema_data, "text/turtle")
        with Session.begin() as session:
            doc = session.scalars(select(Document).where(Document.id == document_id).with_for_update()).first()
            if not doc or doc.status != "processing" or doc.lease_token != x_worker_lease:
                raise HTTPException(409, "worker lease is not active")
            archive_graph(doc)
            doc.graph_key = graph_key
            doc.schema_key = schema_key
            doc.ontology_version = x_ontology_version
            doc.quality_json = json.dumps(quality, ensure_ascii=False)
            doc.reviewed_at = None
            doc.review_note = None
            doc.status = "needs_review"
            doc.stage = None
            doc.error = None
            doc.lease_until = None
            doc.lease_token = None
            doc.updated_at = datetime.now(timezone.utc)
    except Exception:
        try:
            delete_bytes([key for key in (graph_key, schema_key) if key])
        except Exception:
            logger.exception("Could not clean up worker artifacts after a failed commit")
        raise
    return {"id": document_id, "status": "needs_review"}


@app.get("/v1/documents/{document_id}/quality", tags=["Documents"])
def document_quality(document_id: str):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "document not found")
    if doc.quality_json:
        return json.loads(doc.quality_json)
    if doc.status == "completed" and doc.graph_key:
        return {**graph_quality_report(graph_for(document_id)), "legacy_published": True}
    raise HTTPException(409, "quality report is not ready")


@app.post("/v1/documents/{document_id}/publish", response_model=DocumentOut, tags=["Documents"])
def publish_document(document_id: str, decision: PublishRequest):
    with Session.begin() as session:
        doc = session.scalars(select(Document).where(Document.id == document_id).with_for_update()).first()
        if not doc:
            raise HTTPException(404, "document not found")
        if doc.status != "needs_review" or not doc.graph_key or not doc.schema_key or not doc.quality_json:
            raise HTTPException(409, "document is not ready for review publication")
        doc.status = "completed"
        doc.reviewed_at = datetime.now(timezone.utc)
        doc.review_note = decision.note.strip()
        doc.updated_at = doc.reviewed_at
        return document_data(doc)


@app.delete("/v1/documents/{document_id}", status_code=204, tags=["Documents"])
def delete_document(document_id: str):
    with Session.begin() as session:
        doc = session.scalars(select(Document).where(Document.id == document_id).with_for_update()).first()
        if not doc:
            raise HTTPException(404, "document not found")
        if doc.status == "processing":
            raise HTTPException(409, "processing document cannot be deleted yet")
        history = json.loads(doc.graph_history_json) if doc.graph_history_json else []
        delete_bytes([key for key in (doc.source_key, doc.graph_key, doc.schema_key,
                                      *(item["graph_key"] for item in history)) if key])
        session.delete(doc)
    return Response(status_code=204)


@app.get("/v1/documents/{document_id}/graph", tags=["Knowledge Graph"], responses={200: {"content": {"text/turtle": {"schema": {"type": "string"}}}}})
def graph(document_id: str):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "document not found")
    if doc.status not in ("completed", "needs_review") or not doc.graph_key:
        raise HTTPException(409, "graph is not ready")
    graph_data = get_bytes(doc.graph_key)
    parsed = Graph().parse(data=graph_data.decode("utf-8"), format="turtle")
    headers = {"ETag": _graph_etag(doc)}
    if normalize_document_graph(parsed, doc.filename):
        return Response(parsed.serialize(format="turtle"), media_type="text/turtle", headers=headers)
    return Response(graph_data, media_type="text/turtle", headers=headers)


def _graph_etag(doc: Document) -> str:
    raw = f"{doc.graph_key}|{doc.updated_at.isoformat() if doc.updated_at else ''}"
    return '"' + hashlib.sha256(raw.encode()).hexdigest() + '"'


@app.put("/v1/documents/{document_id}/graph", response_model=DocumentOut, tags=["Knowledge Graph"],
         openapi_extra={"requestBody": {"required": True, "content": {"text/turtle": {"schema": {"type": "string"}}}}})
async def replace_document_graph(document_id: str, request: Request, if_match: str | None = Header(default=None)):
    if not if_match:
        raise HTTPException(428, "If-Match from GET /graph is required")
    if not request.headers.get("content-type", "").startswith("text/turtle"):
        raise HTTPException(415, "Content-Type must be text/turtle")
    old = get_document(document_id)
    if not old:
        raise HTTPException(404, "document not found")
    if old.status not in ("completed", "needs_review") or not old.graph_key:
        raise HTTPException(409, "document graph is not ready for correction")
    if if_match != _graph_etag(old):
        raise HTTPException(412, "graph changed; fetch its latest ETag")
    payload = await request.body()
    if not payload or len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "corrected graph exceeds upload limit")
    try:
        parsed = Graph().parse(data=payload.decode("utf-8"), format="turtle")
        normalize_document_graph(parsed, old.filename)
        validate_instance_graph(parsed)
    except Exception as exc:
        raise HTTPException(422, f"invalid corrected graph: {exc}") from exc
    quality = graph_quality_report(parsed, {"manual_correction": True})
    key = put_bytes(f"graphs/{document_id}/corrections/{uuid4().hex}/world.ttl",
                    parsed.serialize(format="turtle").encode("utf-8"), "text/turtle")
    try:
        with Session.begin() as session:
            doc = session.scalars(select(Document).where(Document.id == document_id).with_for_update()).first()
            if not doc or doc.status not in ("completed", "needs_review") or _graph_etag(doc) != if_match:
                raise HTTPException(412, "graph changed; fetch its latest ETag")
            archive_graph(doc)
            doc.graph_key = key
            doc.quality_json = json.dumps(quality, ensure_ascii=False)
            doc.status = "needs_review"
            doc.reviewed_at = None
            doc.review_note = None
            doc.updated_at = datetime.now(timezone.utc)
            return document_data(doc)
    except Exception:
        delete_bytes([key])
        raise


@app.get("/v1/documents/{document_id}/schema", tags=["Knowledge Graph"], responses={200: {"content": {"text/turtle": {"schema": {"type": "string"}}}}})
def schema(document_id: str):
    doc = get_document(document_id)
    if not doc:
        raise HTTPException(404, "document not found")
    if doc.status not in ("completed", "needs_review") or not doc.schema_key:
        raise HTTPException(409, "schema is not ready")
    return Response(valid_schema_turtle(get_bytes(doc.schema_key), document_id), media_type="text/turtle")


@app.post("/v1/documents/{document_id}/retry", status_code=202, response_model=DocumentOut, tags=["Documents"])
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
        doc.quality_json = None
        doc.reviewed_at = None
        doc.review_note = None
        doc.attempts = 0
        doc.lease_until = None
        doc.updated_at = datetime.now(timezone.utc)
        return document_data(doc)


@app.post("/v1/documents/{document_id}/reprocess", status_code=202, response_model=DocumentOut, tags=["Documents"])
def reprocess_document(document_id: str):
    with Session.begin() as session:
        doc = session.scalars(select(Document).where(Document.id == document_id).with_for_update()).first()
        if not doc:
            raise HTTPException(404, "document not found")
        if doc.status in ("queued", "processing"):
            raise HTTPException(409, "document is already queued or processing")
        doc.status = "queued"
        doc.stage = None
        doc.error = None
        doc.quality_json = None
        doc.reviewed_at = None
        doc.review_note = None
        doc.attempts = 0
        doc.lease_until = None
        doc.lease_token = None
        doc.ontology_version = None
        doc.updated_at = datetime.now(timezone.utc)
        return document_data(doc)


@app.get("/v1/ontology", tags=["Domain Ontology"])
def domain_ontology():
    return schema_card()


@app.get("/v1/ontology/ttl", tags=["Domain Ontology"], responses={200: {"content": {"text/turtle": {"schema": {"type": "string"}}}}})
def domain_ontology_ttl():
    return Response(schema_turtle(), media_type="text/turtle")


@app.get("/v1/knowledge-graph/summary", tags=["Domain Ontology"])
def knowledge_graph_summary():
    try:
        return domain_summary()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/v1/knowledge-graph/sparql", tags=["Domain Ontology"], openapi_extra={"requestBody": {"required": True, "content": {"application/sparql-query": {"schema": {"type": "string", "example": "PREFIX la: <https://mini-ontorag.vercel.app/ontology/legal-audit/v1/> SELECT ?s WHERE { ?s a la:Risk } LIMIT 20"}}}}})
async def knowledge_graph_sparql(request: Request):
    try:
        return select_domain_query((await request.body()).decode("utf-8"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/v1/documents/{document_id}/sparql", tags=["Knowledge Graph"], openapi_extra={"requestBody": {"required": True, "content": {"application/sparql-query": {"schema": {"type": "string", "example": "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20"}}}}})
async def sparql(document_id: str, request: Request):
    query = (await request.body()).decode("utf-8")
    try:
        return select_query(document_id, query)
    except FileNotFoundError as exc:
        raise HTTPException(404 if str(exc) == "document not found" else 409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/v1/documents/{document_id}/describe", tags=["Knowledge Graph"])
def describe_iri(document_id: str, iri: str):
    try:
        return {"iri": iri, "properties": describe(document_id, iri)}
    except FileNotFoundError as exc:
        raise HTTPException(404 if str(exc) == "document not found" else 409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
