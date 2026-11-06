import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DATABASE_URL


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = {"schema": "ontorag"} if DATABASE_URL.startswith("postgresql") else {}
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    source_key: Mapped[str] = mapped_column(Text)
    graph_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    graph_history_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    ontology_version: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    stage: Mapped[str | None] = mapped_column(String(24), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


engine_options = {"pool_pre_ping": True}
if DATABASE_URL.startswith("postgresql"):
    engine_options.update(
        pool_size=1,
        max_overflow=0,
        connect_args={"prepare_threshold": None, "sslmode": "require"},
    )
engine = create_engine(DATABASE_URL, **engine_options)
Session = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(78621017)"))
            connection.execute(text("CREATE SCHEMA IF NOT EXISTS ontorag"))
        Base.metadata.create_all(connection)
        schema = "ontorag" if engine.dialect.name == "postgresql" else None
        columns = {column["name"] for column in inspect(connection).get_columns("documents", schema=schema)}
        if "lease_token" not in columns:
            table = "ontorag.documents" if schema else "documents"
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN lease_token VARCHAR(36)"))
        if "ontology_version" not in columns:
            table = "ontorag.documents" if schema else "documents"
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN ontology_version VARCHAR(64)"))
        table = "ontorag.documents" if schema else "documents"
        if "quality_json" not in columns:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN quality_json TEXT"))
        if "graph_history_json" not in columns:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN graph_history_json TEXT"))
        if "reviewed_at" not in columns:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN reviewed_at TIMESTAMP WITH TIME ZONE"))
        if "review_note" not in columns:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN review_note TEXT"))


def document_data(doc: Document) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "sha256": doc.sha256,
        "status": doc.status,
        "stage": doc.stage,
        "error": doc.error,
        "quality": json.loads(doc.quality_json) if doc.quality_json else None,
        "reviewed_at": doc.reviewed_at.isoformat() if doc.reviewed_at else None,
        "review_note": doc.review_note,
        "attempts": doc.attempts,
        "ontology_version": doc.ontology_version,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


def archive_graph(doc: Document) -> None:
    """Retain the previous immutable Blob before replacing a document graph."""
    if not doc.graph_key:
        return
    history = json.loads(doc.graph_history_json) if doc.graph_history_json else []
    history.append({"graph_key": doc.graph_key, "archived_at": datetime.now(timezone.utc).isoformat()})
    doc.graph_history_json = json.dumps(history, ensure_ascii=False)


def get_document(document_id: str) -> Document | None:
    with Session() as session:
        return session.get(Document, document_id)


def list_documents(limit: int = 50) -> list[dict]:
    with Session() as session:
        docs = session.scalars(select(Document).order_by(Document.created_at.desc()).limit(limit)).all()
        return [document_data(doc) for doc in docs]
