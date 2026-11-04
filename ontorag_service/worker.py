import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import httpx
from threading import Event, Thread
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from rdflib import Graph

from sqlalchemy import or_, select, update

from .db import Document, Session, init_db
from .storage import get_bytes, put_bytes
from .domain import ONTOLOGY_VERSION, schema_card, schema_turtle, validate_instance_graph


def _api_client() -> tuple[str, dict[str, str]] | None:
    base = os.getenv("ONTORAG_API_BASE_URL", "").rstrip("/")
    if not base:
        return None
    key = os.getenv("ONTORAG_SERVICE_API_KEY", "")
    if not key:
        raise RuntimeError("ONTORAG_SERVICE_API_KEY is required for worker API storage")
    return base, {"Authorization": f"Bearer {key}"}


def _remote_source(base: str, headers: dict[str, str], document_id: str, token: str) -> bytes:
    with httpx.Client(timeout=90) as client:
        response = client.get(f"{base}/v1/internal/documents/{document_id}/source", headers={**headers, "X-Worker-Lease": token})
        response.raise_for_status()
        return response.content


def _remote_artifacts(base: str, headers: dict[str, str], document_id: str, token: str, graph: Path, schema: Path) -> None:
    with httpx.Client(timeout=120) as client, graph.open("rb") as graph_file, schema.open("rb") as schema_file:
        response = client.post(
            f"{base}/v1/internal/documents/{document_id}/artifacts",
            headers={**headers, "X-Worker-Lease": token, "X-Ontology-Version": ONTOLOGY_VERSION},
            files={"graph": ("world.ttl", graph_file, "text/turtle"), "schema": ("schema.ttl", schema_file, "text/turtle")},
        )
        response.raise_for_status()


def claim_job() -> tuple[str, str] | None:
    now = datetime.now(timezone.utc)
    with Session.begin() as session:
        query = (
            select(Document)
            .where(or_(
                (Document.status == "queued") & or_(Document.lease_until.is_(None), Document.lease_until < now),
                (Document.status == "processing") & (Document.lease_until < now),
            ))
            .where(Document.attempts < 3)
            .order_by(Document.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        doc = session.scalars(query).first()
        if not doc:
            return None
        doc.status = "processing"
        doc.stage = "ingest"
        doc.error = None
        doc.attempts += 1
        token = str(uuid4())
        doc.lease_token = token
        doc.lease_until = now + timedelta(minutes=5)
        doc.updated_at = now
        return doc.id, token


def set_stage(document_id: str, token: str, stage: str) -> None:
    with Session.begin() as session:
        changed = session.execute(update(Document).where(Document.id == document_id, Document.lease_token == token).values(stage=stage, updated_at=datetime.now(timezone.utc))).rowcount
        if changed != 1:
            raise RuntimeError("job lease was lost")


def renew_lease(document_id: str, token: str) -> bool:
    with Session.begin() as session:
        changed = session.execute(update(Document).where(Document.id == document_id, Document.lease_token == token, Document.status == "processing").values(lease_until=datetime.now(timezone.utc) + timedelta(minutes=5))).rowcount
        return changed == 1


def keep_lease(document_id: str, token: str, stop: Event, lost: Event) -> None:
    while not stop.wait(60):
        try:
            if not renew_lease(document_id, token):
                lost.set()
                return
        except Exception:
            # A transient database error may recover before the existing lease expires.
            pass


def _cli() -> str:
    command = shutil.which("ontorag")
    if command:
        return command
    candidate = Path(sys.executable).parent / ("ontorag.exe" if os.name == "nt" else "ontorag")
    if candidate.exists():
        return str(candidate)
    raise RuntimeError("ontorag CLI is not installed")


def _run_cli(cli: str, root: Path, *args: str) -> None:
    env = os.environ.copy()
    env["PATH"] = str(Path(cli).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [cli, *args],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=5400,
    )
    if result.returncode:
        raise RuntimeError(f"OntoRAG {' '.join(args[:1])} failed: {result.stdout[-1800:]}")


def process_job(document_id: str, token: str, lost: Event) -> None:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key or api_key == "在这里填写密钥":
        raise RuntimeError("OPENROUTER_API_KEY is missing")
    with Session() as session:
        doc = session.get(Document, document_id)
        filename, source_key = doc.filename, doc.source_key
    remote = _api_client()
    with tempfile.TemporaryDirectory(prefix="ontorag-") as temp:
        root = Path(temp)
        source = root / "content" / "sources" / filename
        source.parent.mkdir(parents=True)
        source.write_bytes(_remote_source(*remote, document_id, token) if remote else get_bytes(source_key))
        ontology = root / "ontology"
        ontology.mkdir()
        card_path = ontology / "schema_card.json"
        extraction_card = {**schema_card(), "aliases": []}
        card_path.write_text(json.dumps(extraction_card, ensure_ascii=False), encoding="utf-8")
        schema = ontology / "schema.ttl"
        schema.write_bytes(schema_turtle())
        cli = _cli()
        _run_cli(cli, root, "ingest", str(source), "--out", str(root / "dto"))
        if lost.is_set():
            raise RuntimeError("job lease was lost")
        chunks = list((root / "dto" / "chunks").glob("*.jsonl"))
        if len(chunks) != 1 or not chunks[0].read_text(encoding="utf-8").strip():
            raise RuntimeError("OntoRAG did not produce one nonempty chunk file")
        set_stage(document_id, token, "extract")
        graph = ontology / "world.ttl"
        _run_cli(cli, root, "extract-instances", "--chunks", str(chunks[0]), "--schema-card", str(card_path), "--out-ttl", str(graph))
        if lost.is_set():
            raise RuntimeError("job lease was lost")
        if not graph.is_file() or not schema.is_file():
            raise RuntimeError("OntoRAG did not produce schema.ttl and world.ttl")
        parsed = Graph()
        parsed.parse(graph, format="turtle")
        if len(parsed) == 0:
            raise RuntimeError("OntoRAG produced an empty instance graph")
        validate_instance_graph(parsed)
        schema_graph = Graph()
        schema_graph.parse(schema, format="turtle")
        if len(schema_graph) == 0:
            raise RuntimeError("OntoRAG produced an empty schema graph")
        if remote:
            _remote_artifacts(*remote, document_id, token, graph, schema)
            return
        graph_key = put_bytes(f"graphs/{document_id}/{token}/world.ttl", graph.read_bytes(), "text/turtle")
        schema_key = put_bytes(f"graphs/{document_id}/{token}/schema.ttl", schema.read_bytes(), "text/turtle")
    with Session.begin() as session:
        changed = session.execute(update(Document).where(Document.id == document_id, Document.lease_token == token).values(graph_key=graph_key, schema_key=schema_key, ontology_version=ONTOLOGY_VERSION, status="completed", stage=None, error=None, lease_until=None, lease_token=None, updated_at=datetime.now(timezone.utc))).rowcount
        if changed != 1:
            raise RuntimeError("job lease was lost")


def run_once() -> bool:
    claimed = claim_job()
    if not claimed:
        return False
    job_id, token = claimed
    stop, lost = Event(), Event()
    heartbeat = Thread(target=keep_lease, args=(job_id, token, stop, lost), daemon=True)
    heartbeat.start()
    try:
        process_job(job_id, token, lost)
    except Exception as exc:
        with Session.begin() as session:
            doc = session.get(Document, job_id)
            if doc and doc.lease_token == token:
                doc.status = "failed" if doc.attempts >= 3 else "queued"
                doc.error = str(exc)[-1800:]
                doc.stage = None
                doc.lease_until = None if doc.status == "failed" else datetime.now(timezone.utc) + timedelta(minutes=2 ** doc.attempts)
                doc.lease_token = None
                doc.updated_at = datetime.now(timezone.utc)
    finally:
        stop.set()
        heartbeat.join(timeout=2)
    return True


def main() -> None:
    init_db()
    while True:
        if not run_once():
            time.sleep(5)


if __name__ == "__main__":
    main()
