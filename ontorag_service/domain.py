"""The versioned vocabulary shared by legal and audit documents."""

import json
import re
from functools import lru_cache
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD


CARD_PATH = Path(__file__).parent / "resources" / "legal_audit_v1.json"
ONTOLOGY_VERSION = "legal-audit-v1"
PROVENANCE = Namespace("https://ontorag.org/provenance#")
ARTICLE_NUMBER = re.compile(r"^(?:第\s*)?[一二三四五六七八九十百千万零〇两0-9]+(?:之[一二三四五六七八九十0-9]+)?\s*条$")


def remove_number_only_clauses(graph: Graph) -> int:
    """Keep article numbers as provenance, not as domain entities.

    OntoRAG sometimes labels a PolicyClause only ``第X条``. Its outgoing
    semantic targets keep the citation and source; the locator node itself
    is removed from the domain graph. This also normalizes older stored TTL.
    """
    namespace = Namespace(schema_card()["namespace"])
    removed = 0
    for clause in list(graph.subjects(RDF.type, namespace.PolicyClause)):
        labels = [str(label).strip() for label in graph.objects(clause, RDFS.label)]
        numbers = list(graph.objects(clause, namespace.clauseNumber))
        if labels and not all(ARTICLE_NUMBER.fullmatch(label) for label in labels):
            continue
        if not labels and not any(ARTICLE_NUMBER.fullmatch(str(number).strip()) for number in numbers):
            continue
        numbers = [Literal(labels[0])] if labels else numbers
        unambiguous_number = numbers if len(set(numbers)) == 1 else []
        mentions = list(graph.objects(clause, PROVENANCE.hasMention))
        sources = list(graph.objects(clause, PROVENANCE.attestedIn))
        targets = {
            target for predicate, target in graph.predicate_objects(clause)
            if str(predicate).startswith(str(namespace)) and isinstance(target, URIRef)
            and predicate != RDF.type
        }
        for mention in mentions:
            for number in numbers:
                graph.add((mention, PROVENANCE.clauseNumber, number))
        for target in targets:
            for mention in mentions:
                graph.add((target, PROVENANCE.hasMention, mention))
                graph.add((mention, PROVENANCE.mentions, target))
            for source in sources:
                graph.add((target, PROVENANCE.attestedIn, source))
            for number in unambiguous_number:
                graph.add((target, PROVENANCE.sourceClauseNumber, number))
        for triple in list(graph.triples((clause, None, None))):
            graph.remove(triple)
        for triple in list(graph.triples((None, None, clause))):
            graph.remove(triple)
        removed += 1
    return removed


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
