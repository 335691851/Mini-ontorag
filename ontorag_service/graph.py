import json
import re

from rdflib import Graph, URIRef

from .db import get_document
from .storage import get_bytes
from .ontology import valid_schema_turtle


def graph_for(document_id: str) -> Graph:
    doc = get_document(document_id)
    if not doc:
        raise FileNotFoundError("document not found")
    if doc.status != "completed" or not doc.graph_key:
        raise FileNotFoundError("document graph is not ready")
    graph = Graph()
    if doc.schema_key:
        graph.parse(data=valid_schema_turtle(get_bytes(doc.schema_key), document_id).decode("utf-8"), format="turtle")
    graph.parse(data=get_bytes(doc.graph_key).decode("utf-8"), format="turtle")
    return graph


def select_query(document_id: str, query: str) -> dict:
    if len(query) > 10000:
        raise ValueError("query too long")
    start = re.sub(r"(?is)^\s*(?:PREFIX\s+\w*:\s*<[^>]+>\s*|BASE\s*<[^>]+>\s*)*", "", query).strip()
    if not re.match(r"(?i)^SELECT\b", start):
        raise ValueError("only SELECT queries are allowed")
    if re.search(r"(?i)\bSERVICE\b", query):
        raise ValueError("federated SERVICE queries are not allowed")
    limits = re.findall(r"(?i)\bLIMIT\s+(\d+)\b", query)
    if limits and int(limits[-1]) > 500:
        raise ValueError("LIMIT must be 500 or less")
    if not limits:
        query += "\nLIMIT 500"
    result = graph_for(document_id).query(query)
    data = json.loads(result.serialize(format="json"))
    data["results"]["bindings"] = data["results"]["bindings"][:500]
    return data


def describe(document_id: str, iri: str) -> list[dict]:
    if not iri.startswith(("http://", "https://", "urn:")):
        raise ValueError("absolute IRI required")
    graph = graph_for(document_id)
    node = URIRef(iri)
    return [{"predicate": str(p), "object": str(o)} for p, o in list(graph.predicate_objects(node))[:500]]
