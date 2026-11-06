"""Deterministic quality report for a generated document graph."""

from collections import Counter
from datetime import datetime, timezone

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

from .domain import ANNOTATION, ARTICLE_NUMBER, PROVENANCE, schema_card


GENERIC_NAMES = frozenset({"公司", "集团公司", "权属企业", "被审计单位", "相关责任人", "有关部门", "本办法", "本制度"})


def graph_quality_report(graph: Graph, extraction: dict | None = None) -> dict:
    card = schema_card()
    namespace = Namespace(card["namespace"])
    classes = {namespace[item["name"]] for item in card["classes"]}
    object_properties = {namespace[item["name"]] for item in card["object_properties"]}
    entities = {subject for subject, kind in graph.subject_objects(RDF.type) if kind in classes}
    relations = {(subject, predicate, target) for subject, predicate, target in graph
                 if predicate in object_properties and subject in entities and target in entities}
    degree = Counter()
    for subject, _, target in relations:
        degree[subject] += 1
        degree[target] += 1
    problems = Counter()
    examples: dict[str, list[str]] = {}

    def record(code: str, subject) -> None:
        problems[code] += 1
        values = examples.setdefault(code, [])
        if len(values) < 10:
            values.append(str(subject))

    for entity in entities:
        label = str(graph.value(entity, RDFS.label) or "").strip()
        mentions = list(graph.objects(entity, PROVENANCE.hasMention))
        if not label:
            record("missing_label", entity)
        if ARTICLE_NUMBER.fullmatch(label):
            record("number_only_label", entity)
        if label in GENERIC_NAMES:
            record("generic_label", entity)
        if not degree[entity]:
            record("isolated_entity", entity)
        if not list(graph.objects(entity, PROVENANCE.attestedIn)):
            record("missing_source", entity)
        if not mentions:
            record("missing_mention", entity)
        elif not any(graph.value(selector, ANNOTATION.exact) for mention in mentions
                     for target in graph.objects(mention, ANNOTATION.hasTarget)
                     for selector in graph.objects(target, ANNOTATION.hasSelector)):
            record("missing_quote", entity)
    unverified = set(graph.subjects(RDF.type, PROVENANCE.UnverifiedRelation))
    for assertion in unverified:
        record("unverified_relation", assertion)
    for group in set(graph.subjects(RDF.type, PROVENANCE.AmbiguousClauseGroup)):
        record("ambiguous_clause_group", group)

    extraction = extraction or {}
    if extraction.get("issue_counts"):
        for code, count in extraction["issue_counts"].items():
            problems["proposal_" + str(code)] += int(count)
    else:
        for item in extraction.get("issues") or []:
            problems["proposal_" + str(item.get("code", "unknown"))] += 1
    problems["skipped_chunk"] += len(extraction.get("skipped_chunk_ids") or [])
    problems["model_warning"] += int(extraction.get("model_warning_count") or 0)
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "review_required": True,
        "metrics": {"entities": len(entities), "relations": len(relations),
                    "isolated_entities": problems["isolated_entity"], "unverified_relations": len(unverified),
                    "chunks_total": extraction.get("chunks_total"),
                    "chunks_with_proposals": extraction.get("chunks_with_proposals")},
        "issue_counts": {key: count for key, count in sorted(problems.items()) if count},
        "examples": examples,
        "extraction_examples": (extraction.get("issues") or [])[:20],
    }
