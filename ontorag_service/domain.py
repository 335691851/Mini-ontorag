"""The versioned vocabulary shared by legal and audit documents."""

import json
from functools import lru_cache
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD


CARD_PATH = Path(__file__).parent / "resources" / "legal_audit_v1.json"
ONTOLOGY_VERSION = "legal-audit-v1"


@lru_cache(maxsize=1)
def schema_card() -> dict:
    card = json.loads(CARD_PATH.read_text(encoding="utf-8"))
    if card.get("ontology_id") != ONTOLOGY_VERSION:
        raise ValueError("domain ontology version does not match its resource")
    classes = {item["name"] for item in card["classes"]}
    if len(classes) != len(card["classes"]):
        raise ValueError("duplicate domain class")
    for key in ("object_properties", "datatype_properties"):
        names = {item["name"] for item in card[key]}
        if len(names) != len(card[key]):
            raise ValueError(f"duplicate {key}")
    for item in card["object_properties"]:
        if item["domain"] not in classes or item["range"] not in classes:
            raise ValueError(f"invalid object property domain or range: {item['name']}")
    for item in card["datatype_properties"]:
        if item["domain"] not in classes:
            raise ValueError(f"invalid datatype property domain: {item['name']}")
    return card


def validate_instance_graph(graph: Graph) -> None:
    card = schema_card()
    namespace = card["namespace"]
    classes = {namespace + item["name"] for item in card["classes"]}
    properties = {namespace + item["name"] for key in ("object_properties", "datatype_properties") for item in card[key]}
    typed = False
    for _, _, object_ in graph.triples((None, RDF.type, None)):
        if str(object_).startswith(namespace):
            if str(object_) not in classes:
                raise ValueError(f"instance graph uses unknown class: {object_}")
            typed = True
    for _, predicate, _ in graph:
        if str(predicate).startswith(namespace) and str(predicate) not in properties:
            raise ValueError(f"instance graph uses unknown property: {predicate}")
    if not typed:
        raise ValueError("instance graph contains no domain entity")


@lru_cache(maxsize=1)
def schema_turtle() -> bytes:
    card = schema_card()
    namespace = Namespace(card["namespace"])
    graph = Graph()
    graph.bind("la", namespace)
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    ontology = URIRef(card["namespace"].rstrip("/"))
    graph.add((ontology, RDF.type, OWL.Ontology))
    graph.add((ontology, OWL.versionInfo, Literal(ONTOLOGY_VERSION)))
    graph.add((ontology, RDFS.comment, Literal(card["description"], lang="zh")))
    labels = card.get("labels", {})
    for item in card["classes"]:
        term = namespace[item["name"]]
        graph.add((term, RDF.type, OWL.Class))
        graph.add((term, RDFS.label, Literal(labels.get(item["name"], item["name"]), lang="zh")))
        graph.add((term, RDFS.comment, Literal(item["description"], lang="zh")))
        if item.get("parent"):
            graph.add((term, RDFS.subClassOf, namespace[item["parent"]]))
    for item in card["object_properties"]:
        term = namespace[item["name"]]
        graph.add((term, RDF.type, OWL.ObjectProperty))
        graph.add((term, RDFS.domain, namespace[item["domain"]]))
        graph.add((term, RDFS.range, namespace[item["range"]]))
        graph.add((term, RDFS.label, Literal(item["description"], lang="zh")))
    datatypes = {"string": XSD.string, "integer": XSD.integer, "number": XSD.decimal, "boolean": XSD.boolean, "date": XSD.date, "datetime": XSD.dateTime}
    for item in card["datatype_properties"]:
        term = namespace[item["name"]]
        graph.add((term, RDF.type, OWL.DatatypeProperty))
        graph.add((term, RDFS.domain, namespace[item["domain"]]))
        graph.add((term, RDFS.range, datatypes.get(item["range"], XSD.string)))
        graph.add((term, RDFS.label, Literal(item["description"], lang="zh")))
    return graph.serialize(format="turtle").encode("utf-8")
