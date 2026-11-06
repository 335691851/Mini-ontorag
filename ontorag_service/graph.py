import json
import re

from rdflib import Graph, URIRef
from rdflib.namespace import RDF
from sqlalchemy import select

from .db import Document, Session, get_document
from .storage import get_bytes
from .ontology import valid_schema_turtle
from .domain import ONTOLOGY_VERSION, schema_card, schema_turtle, normalize_document_graph, canonicalize_domain_entities


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
    normalize_document_graph(graph, doc.filename)
    return graph


def domain_graph() -> tuple[Graph, list[str]]:
    with Session() as session:
        docs = session.scalars(select(Document).where(Document.status == "completed", Document.ontology_version == ONTOLOGY_VERSION).order_by(Document.created_at).limit(101)).all()
        keys = [(doc.id, doc.graph_key, doc.filename) for doc in docs if doc.graph_key]
    if len(keys) > 100:
        raise ValueError("domain graph currently supports at most 100 completed documents")
    graph = Graph()
    graph.parse(data=schema_turtle().decode("utf-8"), format="turtle")
    for _, key, filename in keys:
        instance = Graph().parse(data=get_bytes(key).decode("utf-8"), format="turtle")
        normalize_document_graph(instance, filename)
        graph += instance
    canonicalize_domain_entities(graph)
    return graph, [doc_id for doc_id, _, _ in keys]


def domain_summary() -> dict:
    graph, document_ids = domain_graph()
    namespace = schema_card()["namespace"]
    classes = {namespace + item["name"] for item in schema_card()["classes"]}
    instances = {str(subject) for subject, _, object_ in graph.triples((None, RDF.type, None)) if str(object_) in classes}
    return {"ontology_version": ONTOLOGY_VERSION, "documents": len(document_ids), "document_ids": document_ids, "triples": len(graph), "entities": len(instances), "classes": len(classes), "object_properties": len(schema_card()["object_properties"])}


def _select_query(graph: Graph, query: str) -> dict:
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
    result = graph.query(query)
    data = json.loads(result.serialize(format="json"))
    data["results"]["bindings"] = data["results"]["bindings"][:500]
    return data


def select_query(document_id: str, query: str) -> dict:
    return _select_query(graph_for(document_id), query)


def select_domain_query(query: str) -> dict:
    return _select_query(domain_graph()[0], query)


def describe(document_id: str, iri: str) -> list[dict]:
    if not iri.startswith(("http://", "https://", "urn:")):
        raise ValueError("absolute IRI required")
    graph = graph_for(document_id)
    node = URIRef(iri)
    return [{"predicate": str(p), "object": str(o)} for p, o in list(graph.predicate_objects(node))[:500]]
