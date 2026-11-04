import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rdflib import Graph

from sqlalchemy import or_, select

from .config import PUBLIC_BASE_IRI
from .db import Document, Session, init_db
from .storage import get_bytes, put_bytes


def claim_job() -> str | None:
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
        doc.stage = "propose"
        doc.error = None
        doc.attempts += 1
        doc.lease_until = now + timedelta(hours=2)
        doc.updated_at = now
        return doc.id


def set_stage(document_id: str, stage: str) -> None:
    with Session.begin() as session:
        doc = session.get(Document, document_id)
        doc.stage = stage
        doc.updated_at = datetime.now(timezone.utc)


def _cli() -> str:
    command = shutil.which("ontorag")
    if command:
        return command
    candidate = Path(sys.executable).parent / ("ontorag.exe" if os.name == "nt" else "ontorag")
    if candidate.exists():
        return str(candidate)
    raise RuntimeError("ontorag CLI is not installed")


def _run_stage(cli: str, stage: str, root: Path) -> None:
    env = os.environ.copy()
    env["PATH"] = str(Path(cli).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [cli, "run-stage", stage, "--root", str(root)],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=5400,
    )
    if result.returncode:
        raise RuntimeError(f"OntoRAG {stage} failed: {result.stdout[-1800:]}")


def process_job(document_id: str) -> None:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key or api_key == "在这里填写密钥":
        raise RuntimeError("OPENROUTER_API_KEY is missing")
    with Session() as session:
        doc = session.get(Document, document_id)
        filename, source_key = doc.filename, doc.source_key
    with tempfile.TemporaryDirectory(prefix="ontorag-") as temp:
        root = Path(temp)
        source = root / "content" / "sources" / filename
        source.parent.mkdir(parents=True)
        source.write_bytes(get_bytes(source_key))
        manifest = {
            "dataset": {
                "slug": document_id.replace("-", ""),
                "name": filename,
                "base_iri": PUBLIC_BASE_IRI.rstrip("/") + "/" + document_id + "/",
                "openrouter_model": os.getenv("OPENROUTER_MODEL", "deepseek-flash"),
                "openrouter_base_url": os.getenv("OPENROUTER_BASE_URL", "https://api.deepseek.com"),
            }
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        cli = _cli()
        _run_stage(cli, "propose", root)
        proposals = root / "ontology" / "proposals.raw.jsonl"
        if not proposals.is_file() or not proposals.read_text(encoding="utf-8").strip():
            raise RuntimeError("OntoRAG produced no schema proposal; check model credentials and response")
        set_stage(document_id, "extract")
        _run_stage(cli, "extract", root)
        graph = root / "ontology" / "world.ttl"
        schema = root / "ontology" / "schema.ttl"
        if not graph.is_file() or not schema.is_file():
            raise RuntimeError("OntoRAG did not produce schema.ttl and world.ttl")
        parsed = Graph()
        parsed.parse(graph, format="turtle")
        if len(parsed) == 0:
            raise RuntimeError("OntoRAG produced an empty instance graph")
        graph_key = put_bytes(f"graphs/{document_id}/world.ttl", graph.read_bytes(), "text/turtle")
        schema_key = put_bytes(f"graphs/{document_id}/schema.ttl", schema.read_bytes(), "text/turtle")
    with Session.begin() as session:
        doc = session.get(Document, document_id)
        doc.graph_key = graph_key
        doc.schema_key = schema_key
        doc.status = "completed"
        doc.stage = None
        doc.error = None
        doc.lease_until = None
        doc.updated_at = datetime.now(timezone.utc)


def run_once() -> bool:
    job_id = claim_job()
    if not job_id:
        return False
    try:
        process_job(job_id)
    except Exception as exc:
        with Session.begin() as session:
            doc = session.get(Document, job_id)
            doc.status = "failed" if doc.attempts >= 3 else "queued"
            doc.error = str(exc)[-1800:]
            doc.stage = None
            doc.lease_until = None if doc.status == "failed" else datetime.now(timezone.utc) + timedelta(minutes=2 ** doc.attempts)
            doc.updated_at = datetime.now(timezone.utc)
    return True


def main() -> None:
    init_db()
    while True:
        if not run_once():
            time.sleep(5)


if __name__ == "__main__":
    main()
