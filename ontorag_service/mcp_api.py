from fastmcp import FastMCP

from .db import get_document, document_data, list_documents
from .graph import describe, select_query

mcp = FastMCP("OntoRAG Knowledge Graph")


@mcp.tool
def list_knowledge_documents(limit: int = 50) -> list[dict]:
    """List uploaded documents and their processing status."""
    return list_documents(min(max(limit, 1), 100))


@mcp.tool
def get_knowledge_document(document_id: str) -> dict:
    """Get one document's processing status by its UUID."""
    doc = get_document(document_id)
    if not doc:
        raise ValueError("document not found")
    return document_data(doc)


@mcp.tool
def sparql_select(document_id: str, query: str) -> dict:
    """Run a read-only SPARQL SELECT against one completed document graph."""
    return select_query(document_id, query)


@mcp.tool
def describe_resource(document_id: str, iri: str) -> list[dict]:
    """Return outgoing predicates and objects for an absolute resource IRI."""
    return describe(document_id, iri)
