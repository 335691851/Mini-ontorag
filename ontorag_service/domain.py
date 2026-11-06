"""The versioned vocabulary shared by legal and audit documents."""

import json
import re
from hashlib import sha256
from functools import lru_cache
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD


CARD_PATH = Path(__file__).parent / "resources" / "legal_audit_v1.json"
ONTOLOGY_VERSION = "legal-audit-v1"
PROVENANCE = Namespace("https://ontorag.org/provenance#")
ARTICLE_NUMBER = re.compile(r"^(?:第\s*)?[一二三四五六七八九十百千万零〇两0-9]+(?:之[一二三四五六七八九十0-9]+)?\s*条$")
CURATED_ORGANIZATIONS = frozenset({"鲁银投资集团股份有限公司"})


def canonical_entity_iri(kind: str, label: str) -> URIRef | None:
    """Canonicalize only exact registered names verified in this corpus."""
    if kind != "Organization" or label not in CURATED_ORGANIZATIONS:
        return None
    digest = sha256(f"{kind}|{label}".encode()).hexdigest()[:20]
    return URIRef(f"{schema_card()['namespace']}{kind}/canonical/{digest}")


def canonicalize_domain_entities(graph: Graph) -> int:
    namespace = Namespace(schema_card()["namespace"])
    replacements = {}
    for subject in graph.subjects(RDF.type, namespace.Organization):
        label = str(graph.value(subject, RDFS.label) or "").strip()
        canonical = canonical_entity_iri("Organization", label)
        if canonical and subject != canonical:
            replacements[subject] = canonical
    if not replacements:
        return 0
    for subject, predicate, object_ in list(graph):
        new_subject = replacements.get(subject, subject)
        new_object = replacements.get(object_, object_)
        if new_subject != subject or new_object != object_:
            graph.remove((subject, predicate, object_))
            graph.add((new_subject, predicate, new_object))
    for original, canonical in replacements.items():
        graph.add((original, OWL.sameAs, canonical))
    return len(replacements)


def normalize_policy_clauses(graph: Graph) -> int:
    """Name substantive rules by content; keep article numbers as provenance.

    OntoRAG often uses only ``第X条`` as a PolicyClause label. Retain its
    relationships when a single clause text supplies the rule's meaning.
    A node conflating multiple clauses is removed because its relationships
    cannot safely be assigned to any one article.
    """
    namespace = Namespace(schema_card()["namespace"])
    changed = 0
    for clause in list(graph.subjects(RDF.type, namespace.PolicyClause)):
        labels = [str(label).strip() for label in graph.objects(clause, RDFS.label)]
        numbers = list(graph.objects(clause, namespace.clauseNumber))
        if labels and not all(ARTICLE_NUMBER.fullmatch(label) for label in labels):
            continue
        if not labels and not any(ARTICLE_NUMBER.fullmatch(str(number).strip()) for number in numbers):
            continue
        numbers = [Literal(labels[0])] if labels else numbers
        unambiguous_number = numbers if len(set(numbers)) == 1 else []
        texts = {re.sub(r"\s+", " ", str(value)).strip() for value in graph.objects(clause, namespace.clauseText)}
        texts.discard("")
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
        if len(texts) == 1 and unambiguous_number:
            content = re.sub(r"^第\s*[一二三四五六七八九十百千万零〇两0-9]+(?:之[一二三四五六七八九十0-9]+)?\s*条[\s:：、.．-]*", "", next(iter(texts))).strip()
            if len(content) >= 8 and not ARTICLE_NUMBER.fullmatch(content):
                for label in list(graph.objects(clause, RDFS.label)):
                    graph.remove((clause, RDFS.label, label))
                graph.add((clause, RDFS.label, Literal(content[:90].rstrip() + ("…" if len(content) > 90 else ""), lang="zh")))
                for number in unambiguous_number:
                    graph.add((clause, PROVENANCE.sourceClauseNumber, number))
                changed += 1
                continue
        for triple in list(graph.triples((clause, None, None))):
            graph.remove(triple)
        for triple in list(graph.triples((None, None, clause))):
            graph.remove(triple)
        changed += 1
    return changed + quarantine_invalid_relations(graph)


def quarantine_invalid_relations(graph: Graph) -> int:
    """Keep malformed assertions inspectable without asserting them as facts."""
    card = schema_card()
    namespace = Namespace(card["namespace"])
    classes = {namespace[item["name"]] for item in card["classes"]}
    parents = {namespace[item["name"]]: namespace[item["parent"]] for item in card["classes"] if item.get("parent")}
    specs = {namespace[item["name"]]: item for item in card["object_properties"]}
    types: dict = {}
    for subject, kind in graph.subject_objects(RDF.type):
        if kind in classes:
            types.setdefault(subject, set()).add(kind)

    def has_type(subject, expected):
        for kind in types.get(subject, ()):
            while kind in classes:
                if kind == expected:
                    return True
                kind = parents.get(kind)
        return False

    count = 0
    for subject, predicate, object_ in list(graph):
        spec = specs.get(predicate)
        if not spec:
            continue
        if has_type(subject, namespace[spec["domain"]]) and isinstance(object_, URIRef) and has_type(object_, namespace[spec["range"]]):
            continue
        graph.remove((subject, predicate, object_))
        digest = sha256(f"{subject}|{predicate}|{object_}".encode()).hexdigest()[:24]
        assertion = PROVENANCE[f"unverified-relation/{digest}"]
        graph.add((assertion, RDF.type, PROVENANCE.UnverifiedRelation))
        graph.add((assertion, RDF.subject, subject))
        graph.add((assertion, RDF.predicate, predicate))
        graph.add((assertion, RDF.object, object_))
        graph.add((subject, PROVENANCE.hasUnverifiedRelation, assertion))
        graph.add((assertion, RDFS.comment, Literal(f"Expected {spec['domain']} -> {spec['range']}; verify against source text")))
        count += 1
    return count


def resolve_reference_numbers(graph: Graph, filename: str) -> int:
    """Keep only a document number confirmed by the uploaded filename."""
    namespace = Namespace(schema_card()["namespace"])
    changed = 0
    for document in graph.subjects(RDF.type, namespace.PolicyDocument):
        for value in list(graph.objects(document, namespace.referenceNumber)):
            compact = re.sub(r"\s+", "", str(value))
            if compact != str(value):
                graph.remove((document, namespace.referenceNumber, value))
                graph.add((document, namespace.referenceNumber, Literal(compact)))
                graph.add((document, PROVENANCE.originalReferenceNumber, value))
                changed += 1
        values = list(graph.objects(document, namespace.referenceNumber))
        if len({str(value) for value in values}) <= 1:
            continue
        title = str(graph.value(document, RDFS.label) or "").strip()
        compact_filename = re.sub(r"\s+", "", filename)
        matches = [value for value in values if str(value) in compact_filename] if title and title in filename else []
        confirmed = matches[0] if len(matches) == 1 else None
        for value in values:
            if value != confirmed:
                graph.remove((document, namespace.referenceNumber, value))
                graph.add((document, PROVENANCE.unresolvedReferenceNumber, value))
                changed += 1
    return changed


def merge_duplicate_policy_documents(graph: Graph) -> int:
    """Merge document mentions only when title and document number agree."""
    namespace = Namespace(schema_card()["namespace"])
    groups: dict[tuple[str, str], list] = {}
    for document in set(graph.subjects(RDF.type, namespace.PolicyDocument)):
        title = str(graph.value(document, RDFS.label) or "").strip()
        values = {re.sub(r"\s+", "", str(value)) for value in graph.objects(document, namespace.referenceNumber)}
        if title and len(values) == 1:
            groups.setdefault((title, next(iter(values))), []).append(document)
    merged = 0
    for documents in groups.values():
        if len(documents) < 2:
            continue
        primary = sorted(documents, key=lambda node: (-len(list(graph.predicate_objects(node))), str(node)))[0]
        for duplicate in documents:
            if duplicate == primary:
                continue
            for subject, predicate, object_ in list(graph):
                if subject == duplicate or object_ == duplicate:
                    graph.remove((subject, predicate, object_))
                    graph.add((primary if subject == duplicate else subject, predicate, primary if object_ == duplicate else object_))
            graph.add((duplicate, OWL.sameAs, primary))
            merged += 1
    return merged


def remove_heading_only_clauses(graph: Graph) -> int:
    """Keep section headings as citations when they assert no rule or relation."""
    namespace = Namespace(schema_card()["namespace"])
    object_properties = {namespace[item["name"]] for item in schema_card()["object_properties"]}
    normative_terms = ("应当", "必须", "不得", "包括", "是指", "负责", "适用", "可以", "需要", "严禁", "禁止", "施行", "执行", "划分", "认定")
    removed = 0
    for clause in list(graph.subjects(RDF.type, namespace.PolicyClause)):
        label = str(graph.value(clause, RDFS.label) or "").strip()
        text = str(graph.value(clause, namespace.clauseText) or "").strip()
        if not label or label != text or len(label) >= 25 or any(mark in label for mark in "。；;，,：:") or any(term in label for term in normative_terms):
            continue
        if any(predicate in object_properties for predicate, _ in graph.predicate_objects(clause)) or any(graph.objects(clause, PROVENANCE.hasUnverifiedRelation)):
            continue
        for mention in graph.objects(clause, PROVENANCE.hasMention):
            graph.add((mention, PROVENANCE.sectionHeading, Literal(label)))
        for triple in list(graph.triples((clause, None, None))):
            graph.remove(triple)
        for triple in list(graph.triples((None, None, clause))):
            graph.remove(triple)
        removed += 1
    return removed


def normalize_document_graph(graph: Graph, filename: str) -> int:
    changed = normalize_policy_clauses(graph)
    changed += remove_heading_only_clauses(graph)
    changed += resolve_reference_numbers(graph, filename)
    changed += merge_duplicate_policy_documents(graph)
    return changed


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
    types: dict = {}
    for subject, _, object_ in graph.triples((None, RDF.type, None)):
        if str(object_).startswith(namespace):
            if str(object_) not in classes:
                raise ValueError(f"instance graph uses unknown class: {object_}")
            typed = True
            types.setdefault(subject, set()).add(object_)
    for _, predicate, _ in graph:
        if str(predicate).startswith(namespace) and str(predicate) not in properties:
            raise ValueError(f"instance graph uses unknown property: {predicate}")
    if not typed:
        raise ValueError("instance graph contains no domain entity")
    class_iris = {URIRef(value) for value in classes}
    parents = {URIRef(namespace + item["name"]): URIRef(namespace + item["parent"]) for item in card["classes"] if item.get("parent")}

    def has_type(subject, expected):
        for kind in types.get(subject, ()):
            while kind in class_iris:
                if kind == expected:
                    return True
                kind = parents.get(kind)
        return False

    for item in card["object_properties"]:
        predicate = URIRef(namespace + item["name"])
        domain = URIRef(namespace + item["domain"])
        range_ = URIRef(namespace + item["range"])
        for subject, object_ in graph.subject_objects(predicate):
            if not has_type(subject, domain) or not isinstance(object_, URIRef) or not has_type(object_, range_):
                raise ValueError(f"invalid {item['name']} domain/range for {subject} -> {object_}")
    for item in card["datatype_properties"]:
        predicate = URIRef(namespace + item["name"])
        domain = URIRef(namespace + item["domain"])
        for subject, object_ in graph.subject_objects(predicate):
            if not has_type(subject, domain) or not isinstance(object_, Literal):
                raise ValueError(f"invalid {item['name']} datatype/domain for {subject}")


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
